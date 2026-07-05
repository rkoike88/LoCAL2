# Critic Participant

`CriticAgent` (`src/local/agents/critic_agent.py`) is a post-generation quality observer. It subscribes to `response.generation`, selects an evaluation rubric based on which tools were called, grades the answer using Prometheus, and publishes `critique.result` with the score, feedback, rubric name, and rubric text.

The critic never blocks the answer delivery path — it operates asynchronously after the generator has already published.

For state machine diagram, see [../diagrams/critic-state-machine.md](../diagrams/critic-state-machine.md).

---

## Role

- **Absolute grading:** assigns a quality score (1–5) and natural language feedback to every generator answer.
- **Dynamic rubric selection:** the rubric used for grading is determined by the tools called in the response. Tools declare their preferred rubric via `critique_rubric_name` and `critique_priority` in their tool schema. The critic picks the highest-priority tool's rubric. When no tool was called, the default `realistic` rubric applies.
- **Grades all responses:** every response with a non-empty answer is graded — including responses that used tools. The rubric adapts to fit the answer type rather than skipping.
- **Never blocks:** on Prometheus failure or score parse failure, publishes `critique.result` with `score=None`. Downstream consumers (MemoryAgent, FastAPI Gateway) treat null as "not graded" and continue normally.
- **XAI engine:** Prometheus generates a rubric-driven narrative per response — not just a score. This feedback, along with the rubric text used, is surfaced in the UI and stored on the engram for audit trail.

---

## Rubric Registry

On startup, the critic broadcasts `tool.schema.request` and subscribes to `tool.schema`. As tools announce their schemas, the critic builds an in-memory registry:

```python
_rubric_registry: dict[str, dict]  # tool_name → {rubric_name, priority}
```

When a `response.generation` arrives, `_resolve_rubric(tool_calls)` scans the list of tools called and picks the one with the highest `priority`. The matching `rubric_name` is looked up in `config/critic.yaml` to get the full rubric text.

---

## Three Rubrics

All rubric texts live in `config/critic.yaml`. Tools declare which rubric applies to their responses via `critique_rubric_name` in their YAML config.

| Rubric name | Applies to | Evaluation focus |
|---|---|---|
| `realistic` | Knowledge-only responses; default | Is the answer realistic, accurate, and not misleading? |
| `style` | Web search, web fetch, datetime, location, search_papers | Is the response well-formatted and comprehensive? (factual accuracy not evaluated — live data) |
| `clarity` | remember_this, persona | Did the assistant clearly confirm what action was taken? |

Tool priority table (higher wins in mixed-tool responses):

| Tool | Rubric | Priority |
|---|---|---|
| `web_search`, `web_fetch` | style | 10 |
| `get_datetime`, `get_location` | style | 8 |
| `search_papers` | style | 7 |
| `search_memory`, `consult_librarian` | realistic | 5 |
| `remember_this` | clarity | 1 |

---

## Grading Flow

1. `response.generation` arrives.
2. Transition: `IDLE → RECEIVING → GRADING`.
3. Call `_resolve_rubric(msg.tool_calls)` → `(rubric_text, rubric_name)`.
4. Build the Prometheus grading prompt with the query, answer, and resolved rubric text.
5. Call `ollama.chat()` (non-streaming) on the Prometheus model.
6. Parse `[RESULT] N` from the response to extract the integer score.
7. Transition: `GRADING → PUBLISHING → IDLE` (or `ERROR → IDLE` on failure).
8. Publish `critique.result` with `{score, feedback, rubric_name, rubric_text, query_id, query}`.

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

The grading prompt template is in `config/critic.yaml` under `grade_prompt`. The rubric text is injected at `{rubric}`. Default `realistic` rubric:

```
[Is the response realistic, accurate, and genuinely helpful?]
Score 1: Presents an unrealistic or harmful outcome as achievable, or is factually wrong. A detailed step-by-step plan for a goal that fails for the vast majority of people scores 1 — structure creates false confidence.
Score 2: Significantly overstates what is realistically achievable.
Score 3: Partially realistic but unclear or incomplete.
Score 4: Largely realistic with only minor gaps a careful reader would notice.
Score 5: Accurate, grounded in what is realistically achievable, complete, and clearly explained.
```

Prometheus outputs: `Feedback: (text) [RESULT] (1-5)`. The critic parses `[RESULT] N` with a regex; if parsing fails, `score=None` is published.

---

## Key Config Knobs

All settings in `config/critic.yaml`.

| Key | Default | Description |
|---|---|---|
| `model` | `prometheus-7b:latest` | Prometheus model tag |
| `temperature` | `0.0` | Deterministic grading |
| `num_ctx` | `32000` | Context window for Prometheus |
| `grade_timeout` | `30` | Seconds; Prometheus can be slow on first call |
| `rubric` | see config | Default rubric text (`realistic`) |
| `style_rubric` | see config | Rubric for live-data responses |
| `clarity_rubric` | see config | Rubric for write-only tool responses |
