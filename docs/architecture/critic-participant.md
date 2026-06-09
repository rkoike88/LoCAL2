# Critic Participant

`CriticAgent` (`src/local/agents/critic_agent.py`) is a post-generation quality observer. It subscribes to `response.generation`, grades each answer using Prometheus (an evaluation-specialist LLM), and publishes `critique.result`.

The critic never blocks the answer delivery path — it operates asynchronously after the generator has already published.

For state machine diagram, see [../diagrams/critic-state-machine.md](../diagrams/critic-state-machine.md).

---

## Role

- **Absolute grading:** assigns a quality score (1–5) to every generator answer using the Prometheus rubric.
- **Never blocks:** on Prometheus failure or score parse failure, publishes `critique.result` with `score=None`. Downstream consumers (MemoryAgent, FastAPI Gateway) treat null as "not graded" and continue normally.

---

## Grading Flow

1. `response.generation` arrives.
2. If `tool_calls` are present in the payload, grading is **skipped** — tool-calling turns are partial answers, not gradeable final responses.
3. Transition: `IDLE → RECEIVING → GRADING`.
4. Build the Prometheus grading prompt with the original query, the answer, and the rubric from config.
5. Call `ollama.chat()` (non-streaming) on the Prometheus model.
6. Parse the `[RESULT] N` pattern from the response to extract the integer score.
7. Transition: `GRADING → PUBLISHING → IDLE`.
8. Publish `critique.result` with `{score, feedback, query_id, query}`.

---

## State Machine

States: `IDLE`, `RECEIVING`, `GRADING`, `PUBLISHING`, `ERROR`

```
IDLE → RECEIVING (RECEIVE)
RECEIVING → GRADING (START_GRADE)
GRADING → PUBLISHING (PUBLISH)
GRADING → ERROR (FAIL)
PUBLISHING → IDLE (RESET)
ERROR → IDLE (RESET)
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
