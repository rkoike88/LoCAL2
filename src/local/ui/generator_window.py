"""GeneratorWindow — floating observability panel for the GeneratorAgent.

Shows: identity bar, live state + context fill, tool registry chips,
collapsible system prompt, state transition log, and a peer registry stub.

Updated via update_status() (generator.status) and append_transition()
(agent.transition where agent == "generator").
"""
from __future__ import annotations

from datetime import datetime

try:
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QPainter, QPen
    from PySide6.QtWidgets import (
        QFrame,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    raise RuntimeError("PySide6 is required.") from exc


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


class _ContextBar(QWidget):
    """Horizontal fill bar showing token_count / num_ctx."""

    _COLOR_LOW  = QColor("#7ec8a4")
    _COLOR_MID  = QColor("#c8a47e")
    _COLOR_HIGH = QColor("#c87e7e")
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

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("generator")
        self.setWindowFlags(Qt.Window)
        self.resize(400, 520)

        self._num_ctx = 1
        self._transitions: list[str] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_header())
        outer.addWidget(self._build_identity_bar())
        outer.addWidget(self._build_state_row())
        outer.addWidget(self._build_context_row())
        outer.addWidget(self._build_divider())
        outer.addWidget(self._build_tools_section())
        outer.addWidget(self._build_divider())
        outer.addWidget(self._build_system_prompt_section())
        outer.addWidget(self._build_divider())
        outer.addWidget(self._build_transitions_section(), 1)
        outer.addWidget(self._build_divider())
        outer.addWidget(self._build_peers_section())

        self._apply_styles()

    # ------------------------------------------------------------------
    # Section builders
    # ------------------------------------------------------------------

    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setObjectName("genHeader")
        w.setFixedHeight(32)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(12, 0, 12, 0)
        lbl = QLabel("generator")
        lbl.setObjectName("genHeaderTitle")
        lay.addWidget(lbl)
        lay.addStretch()
        return w

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
        self._peers_widget.setVisible(False)  # hidden until peers arrive
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
        respondent  = data.get("respondent_id", "A")
        model       = data.get("model", "—")
        temperature = data.get("temperature", 0.0)
        num_ctx     = data.get("num_ctx", 1)
        state       = data.get("state", "idle")
        token_count = data.get("token_count", 0)
        tool_names  = data.get("tool_names") or []
        system_prompt = data.get("system_prompt") or ""

        # Identity bar
        self._identity_label.setText(
            f"{instance_id}  ·  {respondent}  ·  {model}  ·  temp {temperature}  ·  ctx {num_ctx:,}"
        )

        # State badge
        color = _STATE_COLORS.get(state.lower(), "#666666")
        self._state_dot.setStyleSheet(f"color: {color}; font-size: 10px;")
        self._state_label.setText(state.upper())
        self._state_label.setStyleSheet(f"color: {color};")

        # Token / context
        self._num_ctx = max(num_ctx, 1)
        if token_count > 0:
            pct = int(token_count / self._num_ctx * 100)
            self._token_label.setText(f"{token_count:,} / {num_ctx:,}  ({pct}%)")
            self._context_bar.set_fill(token_count / self._num_ctx)
        else:
            self._token_label.setText("")
            self._context_bar.set_fill(0.0)

        # Tool registry
        count = len(tool_names)
        self._tools_header.setText(f"Tools ({count})")
        if tool_names:
            self._tools_label.setText("  ·  ".join(tool_names))
        else:
            self._tools_label.setText("(none registered)")

        # System prompt (set once; collapsed by default)
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
            QFrame#genDivider { color: #1e1e1e; background: #1e1e1e; max-height: 1px; }
            QScrollBar:vertical { background: #0d0d0d; width: 4px; }
            QScrollBar::handle:vertical { background: #333; border-radius: 2px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
        """)
