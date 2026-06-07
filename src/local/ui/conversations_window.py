"""ConversationsWindow — floating panel listing all past conversation sessions.

Lets the user:
  - See all sessions with title, message count, and age
  - Rejoin any session (loads its history back into the active context)
  - Delete a session (with confirmation)

Constructor:
  ConversationsWindow(conversation_service, session_id_getter, rejoin_callback)
"""
from __future__ import annotations

import logging
import time
from typing import Callable

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QMessageBox,
        QPushButton,
        QStyle,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    raise RuntimeError("PySide6 is required.") from exc

logger = logging.getLogger(__name__)

_CURRENT_COLOR = "#7ec8a4"
_ROW_HEIGHT = 28


def _format_age(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


class ConversationsWindow(QWidget):

    def __init__(
        self,
        conversation_service,
        session_id_getter: Callable[[], str | None],
        rejoin_callback: Callable[[str], None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._conv = conversation_service
        self._get_session_id = session_id_getter
        self._rejoin = rejoin_callback

        self.setWindowTitle("conversations")
        self.setMinimumWidth(560)
        self.setMinimumHeight(320)
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # Header bar
        bar = QHBoxLayout()
        title = QLabel("conversations")
        title.setStyleSheet("color:#9dbde8; font-weight:bold; font-size:13px;")
        bar.addWidget(title)
        bar.addStretch()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setFixedWidth(70)
        refresh_btn.clicked.connect(self._refresh)
        bar.addWidget(refresh_btn)
        root.addLayout(bar)

        # Table
        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["Title", "Msgs", "Age", ""])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet(
            "QTableWidget { background:#111; alternate-background-color:#141414; }"
            "QHeaderView::section { background:#1a1a1a; color:#888; border:none; padding:4px 8px; }"
        )
        root.addWidget(self._table)

        # Legend
        legend = QLabel("● = current session     ↩ = rejoin")
        legend.setStyleSheet("color:#555; font-size:11px;")
        root.addWidget(legend)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        sessions = self._conv.list_sessions()
        current_id = self._get_session_id()
        now = time.time()

        self._table.setRowCount(len(sessions))
        for row, s in enumerate(sessions):
            sid = s["session_id"]
            is_current = sid == current_id

            # Title col — with ● marker for current
            title_text = s["title"] or "(no title)"
            if is_current:
                title_text = f"● {title_text}"
            title_item = QTableWidgetItem(title_text)
            title_item.setData(Qt.ItemDataRole.UserRole, sid)
            if is_current:
                title_item.setForeground(Qt.GlobalColor.green)
            self._table.setItem(row, 0, title_item)

            # Msgs col
            msgs_item = QTableWidgetItem(str(s["message_count"]))
            msgs_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(row, 1, msgs_item)

            # Age col
            age = now - s["last_active"]
            age_item = QTableWidgetItem(_format_age(age))
            age_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(row, 2, age_item)

            # Actions col — rejoin + delete buttons in a widget
            actions = QWidget()
            actions_layout = QHBoxLayout(actions)
            actions_layout.setContentsMargins(2, 0, 2, 0)
            actions_layout.setSpacing(4)

            rejoin_btn = QPushButton("↩")
            rejoin_btn.setToolTip("Rejoin this session")
            rejoin_btn.setFixedWidth(28)
            rejoin_btn.setFixedHeight(22)
            rejoin_btn.clicked.connect(lambda checked, s_id=sid: self._on_rejoin(s_id))
            actions_layout.addWidget(rejoin_btn)

            del_btn = QPushButton()
            del_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
            del_btn.setToolTip("Delete this session")
            del_btn.setFixedWidth(28)
            del_btn.setFixedHeight(22)
            del_btn.clicked.connect(lambda checked, s_id=sid, t=title_text: self._on_delete(s_id, t))
            actions_layout.addWidget(del_btn)

            self._table.setCellWidget(row, 3, actions)
            self._table.setRowHeight(row, _ROW_HEIGHT)

    def _on_rejoin(self, session_id: str) -> None:
        self._rejoin(session_id)
        self._refresh()

    def _on_delete(self, session_id: str, title: str) -> None:
        ans = QMessageBox.question(
            self,
            "Delete session",
            f"Delete session:\n\n{title[:80]}\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ans == QMessageBox.StandardButton.Yes:
            self._conv.delete_session(session_id)
            self._refresh()

    # ------------------------------------------------------------------
    # Qt events
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh()
