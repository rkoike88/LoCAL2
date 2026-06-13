"""Observability windows for LoCAL2 tools and agents.

BaseObservabilityWindow — shared chrome: title bar, activity log,
optional YAML settings view toggled by ⚙ / ←.

ToolWindow — concrete subclass for any tool that publishes tool.activity.*
Spawned reactively when tool.schema arrives on the bus.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QHBoxLayout,
        QLabel,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QStackedWidget,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    raise RuntimeError("PySide6 is required.") from exc

from local.protocol.envelope import MessageEnvelope
from local.protocol.messages import ConfigReload, ToolSchemaRequest


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Base window
# ---------------------------------------------------------------------------

class BaseObservabilityWindow(QWidget):
    """Floating window with an activity log and optional YAML settings view.

    Subclasses call append_entry() to add lines to the activity log.
    Pass config_name to enable the ⚙ settings toggle; omit it to hide the gear.
    """

    _ACTIVITY_PAGE = 0
    _SETTINGS_PAGE = 1

    def __init__(
        self,
        title: str,
        publisher=None,
        config_name: str | None = None,
        width: int = 400,
        height: int = 420,
    ) -> None:
        super().__init__()
        self._title = title
        self._publisher = publisher
        self._config_name = config_name
        self._config_path = (
            _repo_root() / "config" / f"{config_name}.yaml" if config_name else None
        )

        self.setWindowTitle(title)
        self.resize(width, height)
        self.setWindowFlags(Qt.Window)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_activity_view())
        if config_name:
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
        self._back_btn.setObjectName("winBackBtn")
        self._back_btn.setFixedSize(28, 28)
        self._back_btn.setFlat(True)
        self._back_btn.clicked.connect(self._show_activity)
        self._back_btn.setVisible(False)

        title_label = QLabel(self._title)
        title_label.setObjectName("winTitle")

        self._gear_btn = QPushButton("⚙")
        self._gear_btn.setObjectName("winGearBtn")
        self._gear_btn.setFixedSize(28, 28)
        self._gear_btn.setFlat(True)
        self._gear_btn.clicked.connect(self._show_settings)
        self._gear_btn.setVisible(bool(self._config_name))

        row = QHBoxLayout()
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(6)
        row.addWidget(self._back_btn)
        row.addWidget(title_label, 1)
        row.addWidget(self._gear_btn)

        header = QWidget()
        header.setObjectName("winHeader")
        header.setLayout(row)
        return header

    def _show_activity(self) -> None:
        self._stack.setCurrentIndex(self._ACTIVITY_PAGE)
        self._back_btn.setVisible(False)
        self._gear_btn.setVisible(bool(self._config_name))

    def _show_settings(self) -> None:
        self._reload_editor()
        self._stack.setCurrentIndex(self._SETTINGS_PAGE)
        self._back_btn.setVisible(True)
        self._gear_btn.setVisible(False)

    # ------------------------------------------------------------------
    # Activity view
    # ------------------------------------------------------------------

    def _build_activity_view(self) -> QWidget:
        self._activity_widget = QWidget()
        self._activity_widget.setObjectName("activityWidget")
        self._activity_layout = QVBoxLayout(self._activity_widget)
        self._activity_layout.setContentsMargins(0, 8, 0, 8)
        self._activity_layout.setSpacing(0)
        self._activity_layout.addStretch()

        self._activity_scroll = QScrollArea()
        self._activity_scroll.setObjectName("activityScroll")
        self._activity_scroll.setWidget(self._activity_widget)
        self._activity_scroll.setWidgetResizable(True)
        self._activity_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._activity_scroll)
        return container

    def append_entry(self, text: str, color: str = "#888888") -> None:
        """Append a text entry to the activity log."""
        label = QLabel(text)
        label.setObjectName("activityItem")
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        label.setStyleSheet(
            f"color: {color}; font-family: 'Menlo','Monaco','Courier New';"
            f"font-size: 12px; padding: 3px 14px;"
        )
        self._activity_layout.insertWidget(self._activity_layout.count() - 1, label)
        self._activity_scroll.verticalScrollBar().setValue(
            self._activity_scroll.verticalScrollBar().maximum()
        )

    @staticmethod
    def _ts() -> str:
        now = datetime.now()
        return now.strftime("%H:%M:%S") + f".{now.microsecond // 100000}"

    # ------------------------------------------------------------------
    # Settings view
    # ------------------------------------------------------------------

    def _build_settings_view(self) -> QWidget:
        self._editor = QPlainTextEdit()
        self._editor.setObjectName("yamlEditor")
        self._editor.setPlaceholderText("Loading config…")

        self._status_label = QLabel("")
        self._status_label.setObjectName("settingsStatus")
        self._status_label.setAlignment(Qt.AlignRight)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("cancelSettingsBtn")
        cancel_btn.setFixedWidth(72)
        cancel_btn.clicked.connect(self._show_activity)

        save_btn = QPushButton("Save")
        save_btn.setObjectName("saveSettingsBtn")
        save_btn.setFixedWidth(72)
        save_btn.clicked.connect(self._save_settings)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(8, 4, 8, 4)
        btn_row.addWidget(self._status_label, 1)
        btn_row.addWidget(cancel_btn)
        btn_row.addSpacing(4)
        btn_row.addWidget(save_btn)

        btn_widget = QWidget()
        btn_widget.setLayout(btn_row)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 4)
        layout.setSpacing(4)
        layout.addWidget(self._editor, 1)
        layout.addWidget(btn_widget)
        return container

    def _reload_editor(self) -> None:
        if not self._config_path:
            return
        try:
            self._editor.setPlainText(self._config_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._editor.setPlainText("")

    def _save_settings(self) -> None:
        text = self._editor.toPlainText()
        try:
            yaml.safe_load(text)
        except yaml.YAMLError as exc:
            self._status_label.setText(f"YAML error: {exc}")
            self._status_label.setStyleSheet("color: #cc4444;")
            return

        reply = QMessageBox.question(
            self,
            f"Save {self._config_name} config",
            f"Save changes to config/{self._config_name}.yaml?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            self._config_path.write_text(text, encoding="utf-8")
        except OSError as exc:
            self._status_label.setText(f"Write error: {exc}")
            self._status_label.setStyleSheet("color: #cc4444;")
            return

        if self._publisher:
            self._publisher.publish(ToolSchemaRequest(), sender_id="ui_settings")
            self._publisher.publish(ConfigReload(target=self._config_name), sender_id="ui_settings")

        self._status_label.setText("Saved ✓")
        self._status_label.setStyleSheet("color: #55aa55;")

    # ------------------------------------------------------------------
    # Styles
    # ------------------------------------------------------------------

    def _apply_styles(self) -> None:
        self.setStyleSheet("""
            BaseObservabilityWindow, QWidget { background: #111111; }
            QWidget#winHeader { background: #1a1a1a; border-bottom: 1px solid #2a2a2a; }
            QLabel#winTitle {
                color: #d4d4d4; font-family: 'Menlo','Monaco','Courier New';
                font-size: 13px; font-weight: bold;
            }
            QPushButton#winBackBtn, QPushButton#winGearBtn {
                color: #7ec8a4; font-size: 15px; background: transparent; border: none;
            }
            QPushButton#winBackBtn:hover, QPushButton#winGearBtn:hover { color: #a8e8c4; }
            QScrollArea#activityScroll { border: none; background: #111111; }
            QWidget#activityWidget { background: #111111; }
            QPlainTextEdit#yamlEditor {
                background: #1a1a1a; color: #ce9178; border: 1px solid #2a2a2a;
                font-family: 'Menlo','Monaco','Courier New'; font-size: 12px;
                border-radius: 4px;
            }
            QPushButton#cancelSettingsBtn {
                background: #1e1e1e; color: #888; border: 1px solid #333;
                border-radius: 4px; padding: 4px 8px; font-size: 12px;
            }
            QPushButton#cancelSettingsBtn:hover { background: #2a2a2a; color: #aaa; }
            QPushButton#saveSettingsBtn {
                background: #1a3a1a; color: #7ec8a4; border: 1px solid #3a5a3a;
                border-radius: 4px; padding: 3px 8px;
            }
            QPushButton#saveSettingsBtn:hover { background: #204020; }
            QLabel#settingsStatus { color: #888; font-size: 11px; }
        """)


# ---------------------------------------------------------------------------
# ToolWindow — spawned reactively when tool.schema arrives
# ---------------------------------------------------------------------------

class ToolWindow(BaseObservabilityWindow):
    """Floating window for a single tool: request/result activity + YAML settings."""

    # Tool schema names that differ from their config file stem.
    _CONFIG_NAME: dict[str, str] = {
        "get_location":  "location",
        "search_papers": "semantic_scholar",
        "search_library": "documents",
    }

    def __init__(self, tool_name: str, publisher=None) -> None:
        config_key = self._CONFIG_NAME.get(tool_name, tool_name)
        config_exists = (_repo_root() / "config" / f"{config_key}.yaml").exists()
        super().__init__(
            title=tool_name,
            publisher=publisher,
            config_name=config_key if config_exists else None,
        )

    def append_activity(self, envelope: MessageEnvelope) -> None:
        payload = envelope.payload or {}
        event = payload.get("event", "")
        ts = self._ts()

        if event == "request":
            parts = [f"[{ts}]  → request"]
            for k, v in payload.items():
                if k not in ("event", "tool"):
                    parts.append(f"   {k}: {str(v)[:120].replace(chr(10), ' ')}")
            self.append_entry("\n".join(parts), color="#7ec8a4")
        elif event == "result":
            snippet = str(payload.get("result", ""))[:200].replace("\n", " ")
            self.append_entry(f"[{ts}]  ← result\n   {snippet}", color="#9dbde8")
        else:
            self.append_entry(f"[{ts}]  {envelope.subject}", color="#555555")
