"""GeneratorWindow — floating observability panel for the GeneratorAgent.

Shows: identity bar, live state + context fill, tool registry chips,
collapsible system prompt, state transition log, and a peer registry stub.

Pass publisher to enable the ⚙ settings panel (config/generator.yaml).
Without a publisher the gear button is hidden and the window is read-only.

Updated via update_status() (generator.status) and append_transition()
(agent.transition where agent == "generator").
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml

try:
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QPainter, QPen
    from PySide6.QtWidgets import (
        QFrame,
        QHBoxLayout,
        QLabel,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QStackedWidget,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    raise RuntimeError("PySide6 is required.") from exc

from local.protocol.messages import ConfigReload


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


_STATE_COLORS: dict[str, str] = {
    "idle":              "#666666",
    "receiving":         "#9dbde8",
    "generating":        "#c8a47e",
    "dispatching_tool":  "#c87ec8",
    "waiting_for_tool":  "#c87ec8",
    "publishing":        "#7ec8a4",
    "error":             "#c87e7e",
}

_MAX_TRANSITIONS = 50

_MAIN_PAGE     = 0
_SETTINGS_PAGE = 1


class _ContextBar(QWidget):
    """Horizontal fill bar showing token_count / num_ctx."""

    _COLOR_LOW   = QColor("#7ec8a4")
    _COLOR_MID   = QColor("#c8a47e")
    _COLOR_HIGH  = QColor("#c87e7e")
    _COLOR_TRACK = QColor("#1e1e1e")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fill = 0.0
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_fill(self, fill: float) -> None:
        self._fill = max(0.0, min(fill, 1.0))
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, self._COLOR_TRACK)
        if self._fill > 0:
            color = (
                self._COLOR_HIGH if self._fill > 0.85 else
                self._COLOR_MID  if self._fill > 0.60 else
                self._COLOR_LOW
            )
            p.fillRect(0, 0, int(w * self._fill), h, color)
        p.end()


class GeneratorWindow(QWidget):
    """Floating window that shows the generator's identity, live state, and tool registry."""

    def __init__(self, publisher=None) -> None:
        super().__init__()
        self._publisher = publisher
        self._num_ctx = 1
        self._transitions: list[str] = []

        self.setWindowTitle("generator")
        self.setWindowFlags(Qt.Window)
        self.resize(400, 520)

        # Main view — all observability sections.
        main_view = QWidget()
        main_layout = QVBoxLayout(main_view)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(self._build_identity_bar())
        main_layout.addWidget(self._build_state_row())
        main_layout.addWidget(self._build_context_row())
        main_layout.addWidget(self._build_divider())
        main_layout.addWidget(self._build_tools_section())
        main_layout.addWidget(self._build_divider())
        main_layout.addWidget(self._build_system_prompt_section())
        main_layout.addWidget(self._build_divider())
        main_layout.addWidget(self._build_transitions_section(), 1)
        main_layout.addWidget(self._build_divider())
        main_layout.addWidget(self._build_peers_section())

        self._stack = QStackedWidget()
        self._stack.addWidget(main_view)
        self._stack.addWidget(self._build_settings_view())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_header())
        outer.addWidget(self._stack, 1)

        self._apply_styles()

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def _build_header(self) -> QWidget:
        self._back_btn = QPushButton("←")
        self._back_btn.setObjectName("genBackBtn")
        self._back_btn.setFixedSize(28, 28)
        self._back_btn.setFlat(True)
        self._back_btn.clicked.connect(self._show_main)
        self._back_btn.setVisible(False)

        lbl = QLabel("generator")
        lbl.setObjectName("genHeaderTitle")

        self._gear_btn = QPushButton("⚙")
        self._gear_btn.setObjectName("genGearBtn")
        self._gear_btn.setFixedSize(28, 28)
        self._gear_btn.setFlat(True)
        self._gear_btn.clicked.connect(self._show_settings)
        self._gear_btn.setVisible(True)

        w = QWidget()
        w.setObjectName("genHeader")
        w.setFixedHeight(32)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(6)
        lay.addWidget(self._back_btn)
        lay.addWidget(lbl, 1)
        lay.addWidget(self._gear_btn)
        return w

    def _show_main(self) -> None:
        self._stack.setCurrentIndex(_MAIN_PAGE)
        self._back_btn.setVisible(False)
        self._gear_btn.setVisible(True)

    def _show_settings(self) -> None:
        config_path = _repo_root() / "config" / "generator.yaml"
        try:
            self._settings_editor.setPlainText(
                config_path.read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            self._settings_editor.setPlainText("")
        self._settings_status.setText("")
        self._stack.setCurrentIndex(_SETTINGS_PAGE)
        self._back_btn.setVisible(True)
        self._gear_btn.setVisible(False)

    # ------------------------------------------------------------------
    # Settings view
    # ------------------------------------------------------------------

    def _build_settings_view(self) -> QWidget:
        self._settings_editor = QPlainTextEdit()
        self._settings_editor.setObjectName("genYamlEditor")
        self._settings_editor.setPlaceholderText("Loading config…")

        self._settings_status = QLabel("")
        self._settings_status.setObjectName("genSettingsStatus")
        self._settings_status.setAlignment(Qt.AlignRight)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("genCancelBtn")
        cancel_btn.setFixedWidth(72)
        cancel_btn.clicked.connect(self._show_main)

        save_btn = QPushButton("Save")
        save_btn.setObjectName("genSaveBtn")
        save_btn.setFixedWidth(72)
        save_btn.clicked.connect(self._save_settings)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(8, 4, 8, 4)
        btn_row.addWidget(self._settings_status, 1)
        btn_row.addWidget(cancel_btn)
        btn_row.addSpacing(4)
        btn_row.addWidget(save_btn)

        btn_widget = QWidget()
        btn_widget.setLayout(btn_row)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 4)
        layout.setSpacing(4)
        layout.addWidget(self._settings_editor, 1)
        layout.addWidget(btn_widget)
        return container

    def _save_settings(self) -> None:
        text = self._settings_editor.toPlainText()
        try:
            yaml.safe_load(text)
        except yaml.YAMLError as exc:
            self._settings_status.setText(f"YAML error: {exc}")
            self._settings_status.setStyleSheet("color: #cc4444;")
            return

        reply = QMessageBox.question(
            self,
            "Save generator config",
            "Save changes to config/generator.yaml?\n\nThe generator will reload the new settings.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        config_path = _repo_root() / "config" / "generator.yaml"
        try:
            config_path.write_text(text, encoding="utf-8")
        except OSError as exc:
            self._settings_status.setText(f"Write error: {exc}")
            self._settings_status.setStyleSheet("color: #cc4444;")
            return

        if self._publisher:
            self._publisher.publish(ConfigReload(target="generator"), sender_id="ui_settings")

        self._settings_status.setText("Saved ✓")
        self._settings_status.setStyleSheet("color: #55aa55;")

    # ------------------------------------------------------------------
    # Main view section builders
    # ------------------------------------------------------------------

    def _build_identity_bar(self) -> QWidget:
        w = QWidget()
        w.setObjectName("genIdentityBar")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(12, 6, 12, 6)
        self._identity_label = QLabel("—")
        self._identity_label.setObjectName("genIdentity")
        self._identity_label.setWordWrap(True)
        lay.addWidget(self._identity_label)
        return w

    def _build_state_row(self) -> QWidget:
        w = QWidget()
        w.setObjectName("genStateRow")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(12, 6, 12, 6)

        self._state_dot = QLabel("●")
        self._state_dot.setObjectName("genStateDot")
        self._state_dot.setFixedWidth(14)

        self._state_label = QLabel("IDLE")
        self._state_label.setObjectName("genStateLabel")

        lay.addWidget(self._state_dot)
        lay.addWidget(self._state_label)
        lay.addStretch()

        self._token_label = QLabel("")
        self._token_label.setObjectName("genTokenLabel")
        lay.addWidget(self._token_label)
        return w

    def _build_context_row(self) -> QWidget:
        w = QWidget()
        w.setObjectName("genContextRow")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 0, 12, 6)
        lay.setSpacing(2)
        self._context_bar = _ContextBar()
        lay.addWidget(self._context_bar)
        return w

    def _build_tools_section(self) -> QWidget:
        w = QWidget()
        w.setObjectName("genSection")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(4)

        self._tools_header = QLabel("Tools (0)")
        self._tools_header.setObjectName("genSectionHeader")
        lay.addWidget(self._tools_header)

        self._tools_label = QLabel("(none registered)")
        self._tools_label.setObjectName("genChips")
        self._tools_label.setWordWrap(True)
        lay.addWidget(self._tools_label)
        return w

    def _build_system_prompt_section(self) -> QWidget:
        w = QWidget()
        w.setObjectName("genSection")
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        hdr_row = QWidget()
        hdr_row.setObjectName("genSectionHdr")
        hdr_lay = QHBoxLayout(hdr_row)
        hdr_lay.setContentsMargins(12, 6, 12, 6)
        hdr_lbl = QLabel("System prompt")
        hdr_lbl.setObjectName("genSectionHeader")
        self._prompt_toggle = QPushButton("▶")
        self._prompt_toggle.setObjectName("genToggleBtn")
        self._prompt_toggle.setFixedSize(18, 18)
        self._prompt_toggle.setFlat(True)
        self._prompt_toggle.setCursor(Qt.PointingHandCursor)
        hdr_lay.addWidget(hdr_lbl)
        hdr_lay.addStretch()
        hdr_lay.addWidget(self._prompt_toggle)
        outer.addWidget(hdr_row)

        self._prompt_box = QTextEdit()
        self._prompt_box.setObjectName("genPromptBox")
        self._prompt_box.setReadOnly(True)
        self._prompt_box.setMaximumHeight(120)
        self._prompt_box.setVisible(False)
        outer.addWidget(self._prompt_box)

        self._prompt_visible = False
        self._prompt_toggle.clicked.connect(self._toggle_prompt)
        return w

    def _build_transitions_section(self) -> QWidget:
        w = QWidget()
        w.setObjectName("genSection")
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        hdr = QLabel("Transitions")
        hdr.setObjectName("genSectionHeader")
        hdr.setContentsMargins(12, 6, 12, 4)
        outer.addWidget(hdr)

        self._transitions_edit = QTextEdit()
        self._transitions_edit.setObjectName("genTransitions")
        self._transitions_edit.setReadOnly(True)
        outer.addWidget(self._transitions_edit, 1)
        return w

    def _build_peers_section(self) -> QWidget:
        self._peers_widget = QWidget()
        self._peers_widget.setObjectName("genSection")
        self._peers_widget.setVisible(False)
        lay = QHBoxLayout(self._peers_widget)
        lay.setContentsMargins(12, 6, 12, 6)
        lbl = QLabel("Peers")
        lbl.setObjectName("genSectionHeader")
        self._peers_detail = QLabel("(none detected)")
        self._peers_detail.setObjectName("genChips")
        lay.addWidget(lbl)
        lay.addStretch()
        lay.addWidget(self._peers_detail)
        return self._peers_widget

    def _build_divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setObjectName("genDivider")
        return line

    # ------------------------------------------------------------------
    # Public update API
    # ------------------------------------------------------------------

    def update_status(self, data: dict) -> None:
        """Handle generator.status payload — updates all panels except transitions log."""
        instance_id = data.get("instance_id", "—")
        model       = data.get("model", "—")
        temperature = data.get("temperature", 0.0)
        num_ctx     = data.get("num_ctx", 1)
        state       = data.get("state", "idle")
        token_count = data.get("token_count", 0)
        tool_names  = data.get("tool_names") or []
        system_prompt = data.get("system_prompt") or ""

        self._identity_label.setText(
            f"{instance_id}  ·  {model}  ·  temp {temperature}  ·  ctx {num_ctx:,}"
        )

        color = _STATE_COLORS.get(state.lower(), "#666666")
        self._state_dot.setStyleSheet(f"color: {color}; font-size: 10px;")
        self._state_label.setText(state.upper())
        self._state_label.setStyleSheet(f"color: {color};")

        self._num_ctx = max(num_ctx, 1)
        if token_count > 0:
            pct = int(token_count / self._num_ctx * 100)
            self._token_label.setText(f"{token_count:,} / {num_ctx:,}  ({pct}%)")
            self._context_bar.set_fill(token_count / self._num_ctx)
        else:
            self._token_label.setText("")
            self._context_bar.set_fill(0.0)

        count = len(tool_names)
        self._tools_header.setText(f"Tools ({count})")
        self._tools_label.setText(
            "  ·  ".join(tool_names) if tool_names else "(none registered)"
        )

        if system_prompt and not self._prompt_box.toPlainText():
            self._prompt_box.setPlainText(system_prompt)

    def append_transition(self, data: dict) -> None:
        """Handle agent.transition payload for the generator agent."""
        now = datetime.now()
        ts = now.strftime("%H:%M:%S")
        from_s = data.get("from", "?")
        to_s   = data.get("to", "?")
        line = f"[{ts}]  {from_s} → {to_s}"

        self._transitions.append(line)
        if len(self._transitions) > _MAX_TRANSITIONS:
            self._transitions = self._transitions[-_MAX_TRANSITIONS:]

        cursor = self._transitions_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        if self._transitions_edit.toPlainText():
            cursor.insertText("\n")
        cursor.insertText(line)
        self._transitions_edit.setTextCursor(cursor)
        self._transitions_edit.verticalScrollBar().setValue(
            self._transitions_edit.verticalScrollBar().maximum()
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _toggle_prompt(self) -> None:
        self._prompt_visible = not self._prompt_visible
        self._prompt_box.setVisible(self._prompt_visible)
        self._prompt_toggle.setText("▼" if self._prompt_visible else "▶")

    def _apply_styles(self) -> None:
        self.setStyleSheet("""
            QWidget { background: #0d0d0d; color: #ececec; font-size: 13px; }
            QWidget#genHeader { background: #171717; border-bottom: 1px solid #222; }
            QLabel#genHeaderTitle {
                color: #888; font-family: "Menlo","Monaco","Courier New";
                font-size: 11px; letter-spacing: 1px;
            }
            QPushButton#genBackBtn, QPushButton#genGearBtn {
                background: transparent; color: #555; border: none;
                font-size: 14px;
            }
            QPushButton#genBackBtn:hover, QPushButton#genGearBtn:hover { color: #aaa; }
            QWidget#genIdentityBar { background: #111; border-bottom: 1px solid #1e1e1e; }
            QLabel#genIdentity {
                color: #7ec8a4;
                font-family: "Menlo","Monaco","Courier New"; font-size: 11px;
            }
            QWidget#genStateRow { background: #0d0d0d; }
            QLabel#genStateLabel {
                font-family: "Menlo","Monaco","Courier New"; font-size: 12px;
                font-weight: bold;
            }
            QLabel#genTokenLabel {
                color: #555; font-family: "Menlo","Monaco","Courier New"; font-size: 11px;
            }
            QWidget#genSection { background: #0d0d0d; }
            QWidget#genSectionHdr { background: #0d0d0d; }
            QLabel#genSectionHeader {
                color: #555; font-family: "Menlo","Monaco","Courier New"; font-size: 11px;
                letter-spacing: 1px;
            }
            QLabel#genChips {
                color: #888; font-family: "Menlo","Monaco","Courier New"; font-size: 11px;
            }
            QPushButton#genToggleBtn {
                background: transparent; color: #555; border: none;
                font-family: "Menlo","Monaco","Courier New"; font-size: 10px;
            }
            QPushButton#genToggleBtn:hover { color: #888; }
            QTextEdit#genPromptBox {
                background: #0a0a0a; color: #666; border: 1px solid #1e1e1e;
                border-radius: 3px; font-family: "Menlo","Monaco","Courier New";
                font-size: 11px; padding: 6px;
            }
            QTextEdit#genTransitions {
                background: #0a0a0a; color: #555; border: none;
                font-family: "Menlo","Monaco","Courier New"; font-size: 11px;
                padding: 6px;
            }
            QPlainTextEdit#genYamlEditor {
                background: #0a0a0a; color: #c8c8a4; border: 1px solid #1e1e1e;
                border-radius: 3px; font-family: "Menlo","Monaco","Courier New";
                font-size: 12px; padding: 6px;
            }
            QPushButton#genSaveBtn {
                background: #1e3a2a; color: #7ec8a4; border: 1px solid #2a5a3a;
                border-radius: 4px; padding: 4px 8px; font-size: 12px;
            }
            QPushButton#genSaveBtn:hover { background: #2a5a3a; }
            QPushButton#genCancelBtn {
                background: #1e1e1e; color: #888; border: 1px solid #333;
                border-radius: 4px; padding: 4px 8px; font-size: 12px;
            }
            QPushButton#genCancelBtn:hover { background: #2a2a2a; color: #aaa; }
            QLabel#genSettingsStatus {
                color: #555; font-family: "Menlo","Monaco","Courier New"; font-size: 11px;
            }
            QFrame#genDivider { color: #1e1e1e; background: #1e1e1e; max-height: 1px; }
            QScrollBar:vertical { background: #0d0d0d; width: 4px; }
            QScrollBar::handle:vertical { background: #333; border-radius: 2px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
        """)
