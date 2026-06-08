# Critic Participant

`CriticAgent` (`src/local/agents/critic_agent.py`) is a post-generation quality observer. It subscribes to `response.generation`, grades each answer using Prometheus (an evaluation-specialist LLM), and publishes `critique.result`. When both RespondentA and RespondentB answers arrive for the same query, it also runs a pairwise comparison and publishes `pairwise.result`.

The critic never blocks the answer delivery path — it operates asynchronously after the generator has already published.

For state machine diagram, see [../diagrams/critic-state-machine.md](../diagrams/critic-state-machine.md).

---

## Role

- **Absolute grading:** assigns a quality score (1–5) to every RespondentA answer using the Prometheus rubric.
- **Pairwise comparison:** when RespondentB is running, compares A and B answers head-to-head and declares a winner.
- **Never blocks:** on Prometheus failure or score parse failure, publishes `critique.result` with `score=None`. Downstream consumers (MemoryAgent, UI) treat null as "not graded" and continue normally.

---

## Absolute Grading Flow

1. `response.generation` arrives.
2. If `tool_calls` are present in the payload, grading is **skipped** — tool-calling turns are partial answers, not gradeable final responses.
3. If `respondent_id == "B"`, the answer is buffered for pairwise (see below) but not graded absolutely.
4. Transition: `IDLE → RECEIVING → GRADING`.
5. Build the Prometheus grading prompt with the original query, the answer, and the rubric from config.
6. Call `ollama.chat()` (non-streaming) on the Prometheus model.
7. Parse the `[RESULT] N` pattern from the response to extract the integer score.
8. Transition: `GRADING → PUBLISHING → IDLE`.
9. Publish `critique.result` with `{score, feedback, query_id, query, respondent_id}`.

---

## Pairwise Comparison Flow

CriticAgent maintains a `_pairwise_buffer`: a dict keyed by `correlation_id`, holding A and B entries as they arrive. The buffer evicts the oldest entry when it exceeds 100 entries.

When both A and B entries are present for the same `correlation_id`:

1. Transition: `IDLE → PAIRWISE_GRADING`.
2. Build the pairwise Prometheus prompt with the query, answer A, and answer B.
3. Call `ollama.chat()` (non-streaming).
4. Parse the `[RESULT] A` or `[RESULT] B` pattern.
5. Transition: `PAIRWISE_GRADING → PUBLISHING → IDLE`.
6. Publish `pairwise.result` with `{winner, query_id_a, query_id_b, feedback}`.

MemoryAgent receives `pairwise.result` and annotates both engrams with `pairwise_winner: True/False`.

---

## State Machine

States: `IDLE`, `RECEIVING`, `GRADING`, `PAIRWISE_GRADING`, `PUBLISHING`, `ERROR`

```
IDLE → RECEIVING (RECEIVE)
RECEIVING → GRADING (START_GRADE)
GRADING → PUBLISHING (PUBLISH)
GRADING → ERROR (FAIL)
PUBLISHING → IDLE (RESET)
ERROR → IDLE (RESET)

IDLE → PAIRWISE_GRADING (START_PAIRWISE)
PAIRWISE_GRADING → PUBLISHING (PUBLISH)
PAIRWISE_GRADING → ERROR (FAIL)
```

---

## Prometheus Grading Prompt

The rubric is loaded from `config/critic.yaml`. Default:

```
[Is the response accurate, helpful, and well-reasoned?]
Score 1: The response is incorrect, harmful, or completely unhelpful.
Score 2: The response is mostly wrong or missing important information.
Score 3: The response is partially correct but incomplete or unclear.
Score 4: The response is mostly correct with minor gaps.
Score 5: The response is accurate, complete, and clearly explained.
```

Prometheus is instructed to output: `Feedback: (text) [RESULT] (1-5)`. The critic parses `[RESULT] N` with a regex; if parsing fails, `score=None` is published.

---

## Key Config Knobs

All settings in `config/critic.yaml`.

| Key | Default | Description |
|---|---|---|
| `model` | `prometheus-7b:latest` | Prometheus model tag |
| `temperature` | `0.0` | Deterministic grading |
| `num_ctx` | `4096` | Context window for Prometheus |
| `grade_timeout` | `30` | Seconds; Prometheus can be slow on first call |
| `rubric` | see config | Injected into every grading prompt |
