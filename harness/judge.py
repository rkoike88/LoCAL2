"""PairwiseJudge — Prometheus pairwise comparison for harness runs."""
from __future__ import annotations

import logging
import re

import ollama
import yaml
from pathlib import Path

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent / "config.yaml"

_SYSTEM_PROMPT = (
    "You are a fair judge assistant assigned to deliver insightful feedback that compares "
    "individual performances, highlighting how each stands relative to others within the same cohort."
)

_PROMPT = """\
###Task Description:
An instruction (might include an Input inside it), two responses to evaluate (denoted as Response A and Response B), a reference answer, and an evaluation criteria are given.
1. Write a detailed feedback that assess the quality of the two responses strictly based on the given evaluation criteria, not evaluating in general.
2. Make comparisons between Response A, Response B, and the Reference Answer. Instead of examining Response A and Response B separately, go straight to the point and mention about the commonalities and differences between them.
3. After writing the feedback, indicate the better response, either "A" or "B".
4. The output format should look as follows: "Feedback: (write a feedback for criteria) [RESULT] (Either "A" or "B")"
5. Please do not generate any other opening, closing, and explanations.

###The instruction to evaluate:
{query}

###Response A to evaluate:
{arm_a}

###Response B to evaluate:
{arm_b}

###Reference Answer:
{reference_answer}

###Evaluation Criteria:
{rubric}

###Feedback: """


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
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            stream=False,
            options={"temperature": self._temperature, "num_ctx": self._num_ctx, "keep_alive": -1},
        )
        text = resp.message.content or ""
        verdict = self._parse(text)
        logger.info("PairwiseJudge: verdict=%s", verdict)
        return {"verdict": verdict, "feedback": text}

    def judge_both(
        self,
        query: str,
        local2_answer: str,
        native_answer: str,
        reference_answer: str = "",
        rubric: str | None = None,
    ) -> dict:
        """Run Prometheus twice (swapping positions) to cancel position-A bias.

        Pass 1: local2=A, native=B
        Pass 2: native=A, local2=B

        Win condition: must win both passes. Split → tie.

        Returns:
            verdict: combined "local2_better" | "native_better" | "tie"
            verdict_pass1/2: per-pass translated verdict
            feedback_pass1/2: full Prometheus text per pass
            t_judge_start / t_judge_start_pass2: wall-clock start times
        """
        import time as _time
        t1 = _time.time()
        p1 = self.judge(query, local2_answer, native_answer, reference_answer, rubric)
        t2 = _time.time()
        p2 = self.judge(query, native_answer, local2_answer, reference_answer, rubric)

        # Translate raw a_better/b_better → local2/native labels per position
        vp1 = {"a_better": "local2_better", "b_better": "native_better"}.get(p1["verdict"], "tie")
        vp2 = {"a_better": "native_better", "b_better": "local2_better"}.get(p2["verdict"], "tie")

        if vp1 == "local2_better" and vp2 == "local2_better":
            combined = "local2_better"
        elif vp1 == "native_better" and vp2 == "native_better":
            combined = "native_better"
        else:
            combined = "tie"

        return {
            "verdict": combined,
            "verdict_pass1": vp1,
            "feedback_pass1": p1["feedback"],
            "verdict_pass2": vp2,
            "feedback_pass2": p2["feedback"],
            "t_judge_start": t1,
            "t_judge_start_pass2": t2,
        }

    @staticmethod
    def _parse(text: str) -> str:
        m = re.search(r"\[RESULT\]\s*(A|B)", text, re.IGNORECASE)
        if not m:
            return "tie"
        r = m.group(1).lower()
        return {"a": "a_better", "b": "b_better"}.get(r, "tie")
