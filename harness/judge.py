"""PairwiseJudge — Prometheus pairwise comparison for harness runs."""
from __future__ import annotations

import logging
import re

import ollama
import yaml
from pathlib import Path

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent / "config.yaml"

_PROMPT = """\
###Task Description:
An instruction, two responses (A and B), an optional reference answer, and an evaluation rubric are given.
1. Write detailed feedback comparing both responses strictly based on the rubric.
2. Where a reference answer is provided, use it as the quality anchor.
3. Indicate the better response: "[RESULT] A", "[RESULT] B", or "[RESULT] tie".
4. Output format: "Feedback: (your feedback) [RESULT] (A or B or tie)"
5. Do not add any other text.

###Instruction:
{query}

###Response A:
{arm_a}

###Response B:
{arm_b}

###Reference Answer:
{reference_answer}

###Evaluation Criteria:
{rubric}

###Feedback:"""


class PairwiseJudge:
    def __init__(self) -> None:
        cfg = yaml.safe_load(_CONFIG_PATH.read_text())
        j = cfg.get("judge", {})
        self._model: str = j.get("model", "prometheus-7b:latest")
        self._temperature: float = j.get("temperature", 0.0)
        self._num_ctx: int = j.get("num_ctx", 32000)
        self._rubric: str = j.get("rubric", "[Which response is more helpful and accurate?]")

    def judge(
        self,
        query: str,
        arm_a: str,
        arm_b: str,
        reference_answer: str = "",
        rubric: str | None = None,
    ) -> dict:
        """Compare arm_a vs arm_b. Returns {verdict, feedback}.

        verdict is one of: 'a_better', 'tie', 'b_better'.
        If reference_answer is provided it is injected into the prompt as the
        quality anchor. rubric overrides the config default when supplied.
        """
        prompt = _PROMPT.format(
            query=query,
            arm_a=arm_a,
            arm_b=arm_b,
            reference_answer=reference_answer or "(none provided)",
            rubric=rubric if rubric is not None else self._rubric,
        )
        logger.info("PairwiseJudge: running %s", self._model)
        resp = ollama.chat(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            stream=False,
            options={"temperature": self._temperature, "num_ctx": self._num_ctx},
        )
        text = resp.message.content or ""
        verdict = self._parse(text)
        logger.info("PairwiseJudge: verdict=%s", verdict)
        return {"verdict": verdict, "feedback": text}

    @staticmethod
    def _parse(text: str) -> str:
        m = re.search(r"\[RESULT\]\s*(A|B|tie)", text, re.IGNORECASE)
        if not m:
            return "tie"
        r = m.group(1).lower()
        return {"a": "a_better", "b": "b_better"}.get(r, "tie")
