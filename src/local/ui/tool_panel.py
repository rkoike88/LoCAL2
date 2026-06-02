"""ToolPanel — per-tool UI panel with activity log and YAML settings editor."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QHBoxLayout,
        QLabel,
        QPlainTextEdit,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    raise RuntimeError("PySide6 is required.") from exc

from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import CONFIG_RELOAD, TOOL_SCHEMA_REQUEST


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


class ToolPanel(QWidget):
    """Panel for a single tool: activity log tab + YAML settings tab."""

    def __init__(self, tool_name: str, config_name: str, publisher) -> None:
        super().__init__()
        self._tool_name = tool_name
        self._config_name = config_name
        self._publisher = publisher
        self._config_path = _repo_root() / "config" / f"{config_name}.yaml"

        tabs = QTabWidget()
        tabs.setObjectName("toolTabs")

        tabs.addTab(self._build_activity_tab(), "Activity")
        tabs.addTab(self._build_settings_tab(), "Settings")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(tabs)

    # ------------------------------------------------------------------
    # Activity tab
    # ------------------------------------------------------------------

    def _build_activity_tab(self) -> QWidget:
        self._activity_widget = QWidget()
        self._activity_widget.setObjectName("activityWidget")
        self._activity_layout = QVBoxLayout(self._activity_widget)
        self._activity_layout.setContentsMargins(0, 8, 0, 8)
        self._activity_layout.setSpacing(0)
        self._activity_layout.addStretch()

        scroll = QScrollArea()
        scroll.setObjectName("activityScroll")
        scroll.setWidget(self._activity_widget)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._activity_scroll = scroll

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)
        return container

    def append_activity(self, envelope: MessageEnvelope) -> None:
        payload = envelope.payload or {}
        event = payload.get("event", "")
        now = datetime.now()
        ts = now.strftime("%H:%M:%S") + f".{now.microsecond // 100000}"

        if event == "request":
            parts = [f"[{ts}]  → request"]
            for k, v in payload.items():
                if k not in ("event", "tool"):
                    snippet = str(v)[:120].replace("\n", " ")
                    parts.append(f"   {k}: {snippet}")
            text = "\n".join(parts)
            color = "#888888"
        elif event == "result":
            result_snippet = str(payload.get("result", ""))[:200].replace("\n", " ")
            text = f"[{ts}]  ← result\n   {result_snippet}"
            color = "#aaaaaa"
        else:
            text = f"[{ts}]  {envelope.subject}"
            color = "#555555"

        label = QLabel(text)
        label.setObjectName("activityItem")
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        label.setStyleSheet(
            f"color: {color}; font-family: 'Menlo', 'Monaco', 'Courier New'; "
            f"font-size: 12px; padding: 3px 16px;"
        )
        self._activity_layout.insertWidget(self._activity_layout.count() - 1, label)
        self._activity_scroll.verticalScrollBar().setValue(
            self._activity_scroll.verticalScrollBar().maximum()
        )

    # ------------------------------------------------------------------
    # Settings tab
    # ------------------------------------------------------------------

    def _build_settings_tab(self) -> QWidget:
        self._editor = QPlainTextEdit()
        self._editor.setObjectName("yamlEditor")
        self._editor.setPlaceholderText("Loading config…")
        self._reload_editor()

        self._status_label = QLabel("")
        self._status_label.setObjectName("settingsStatus")
        self._status_label.setAlignment(Qt.AlignRight)

        save_btn = QPushButton("Save")
        save_btn.setObjectName("saveSettingsBtn")
        save_btn.setFixedWidth(80)
        save_btn.clicked.connect(self._save_settings)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(8, 4, 8, 4)
        btn_row.addWidget(self._status_label, 1)
        btn_row.addWidget(save_btn)

        btn_widget = QWidget()
        btn_widget.setObjectName("settingsBtnRow")
        btn_widget.setLayout(btn_row)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 4)
        layout.setSpacing(4)
        layout.addWidget(self._editor, 1)
        layout.addWidget(btn_widget)
        return container

    def _reload_editor(self) -> None:
        try:
            text = self._config_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            text = ""
        self._editor.setPlainText(text)

    def _save_settings(self) -> None:
        text = self._editor.toPlainText()
        try:
            yaml.safe_load(text)  # validate before writing
        except yaml.YAMLError as exc:
            self._status_label.setText(f"YAML error: {exc}")
            self._status_label.setStyleSheet("color: #cc4444;")
            return

        try:
            self._config_path.write_text(text, encoding="utf-8")
        except OSError as exc:
            self._status_label.setText(f"Write error: {exc}")
            self._status_label.setStyleSheet("color: #cc4444;")
            return

        # Signal tools to reload their schema
        self._publisher.publish(MessageEnvelope.create(
            message_type="schema_request",
            subject=TOOL_SCHEMA_REQUEST,
            sender_id="ui_settings",
            payload={"config": self._config_name},
        ))
        self._publisher.publish(MessageEnvelope.create(
            message_type="config_reload",
            subject=CONFIG_RELOAD,
            sender_id="ui_settings",
            payload={"config": self._config_name},
        ))

        self._status_label.setText("Saved")
        self._status_label.setStyleSheet("color: #55aa55;")
