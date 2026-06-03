"""CriticWindow — floating observability window for CriticAgent.

Shows absolute grades (critique.result), pairwise results (pairwise.result),
and state transitions (agent.transition) in a live activity log.
Settings view allows editing critic.yaml.
"""
from __future__ import annotations

from local.ui.tool_window import BaseObservabilityWindow


_SCORE_COLORS = {
    1: "#cc4444",
    2: "#cc7744",
    3: "#ccaa44",
    4: "#88aa44",
    5: "#44aa66",
}


class CriticWindow(BaseObservabilityWindow):

    def __init__(self, publisher=None) -> None:
        super().__init__(title="critic", publisher=publisher, config_name="critic")

    def append_critique(self, data: dict) -> None:
        """Called when critique.result arrives."""
        ts = self._ts()
        score = data.get("score")
        respondent = data.get("respondent_id", "A")
        query = (data.get("query") or "")[:60].replace("\n", " ")
        feedback = (data.get("feedback") or "")[:100].replace("\n", " ")

        score_str = f"● {score}/5" if score is not None else "● —"
        color = _SCORE_COLORS.get(score, "#888888")

        lines = [f"[{ts}]  {score_str}  respondent={respondent}"]
        if query:
            lines.append(f"   Q: {query}")
        if feedback:
            lines.append(f"   {feedback}")
        self.append_entry("\n".join(lines), color=color)

    def append_pairwise(self, data: dict) -> None:
        """Called when pairwise.result arrives."""
        ts = self._ts()
        winner = data.get("winner") or "?"
        qid_a = (data.get("query_id_a") or "")[:8]
        qid_b = (data.get("query_id_b") or "")[:8]
        self.append_entry(
            f"[{ts}]  ⟺ pairwise  winner={winner}\n   A: {qid_a}  B: {qid_b}",
            color="#9dbde8",
        )

    def append_transition(self, data: dict) -> None:
        """Called when agent.transition arrives for this agent."""
        ts = self._ts()
        from_s = data.get("from", "?")
        action = data.get("action", "?")
        to_s = data.get("to", "?")
        self.append_entry(
            f"[{ts}]  {from_s} → {to_s}  ({action})",
            color="#333333",
        )
