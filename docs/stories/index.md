# Stories

Stories are the acceptance criteria for LoCAL2 features. Each YAML file defines multi-turn conversations with expected bus events and answer content. They are the ground truth for "does the system behave correctly end-to-end."

Stories live in `tests/stories/`. Run them against the live stack after significant changes.

| Story | File | What it tests |
|---|---|---|
| S1 | [s1_basic_qa.yaml](../../tests/stories/s1_basic_qa.yaml) | Basic factual Q&A — generator answers without tools |
| S2 | [s2_multi_turn.yaml](../../tests/stories/s2_multi_turn.yaml) | Multi-turn conversation — pronoun resolution, follow-up questions |
| S3 | [s3_web_search.yaml](../../tests/stories/s3_web_search.yaml) | Web search — Gemma calls web_search for live data |
| S4 | [s4_web_fetch.yaml](../../tests/stories/s4_web_fetch.yaml) | Web fetch — Gemma calls web_fetch to read a specific URL |
| S5 | [s5_memory_episodic.yaml](../../tests/stories/s5_memory_episodic.yaml) | Episodic memory recall — Gemma calls search_memory for prior preferences |
| S6 | [s6_critic_absolute.yaml](../../tests/stories/s6_critic_absolute.yaml) | Critic absolute grading — critique.result published after every answer |
| ~~S7~~ | ~~s7_pairwise_respondents.yaml~~ | Dual respondents + pairwise — removed with Phase 5 rollback (2026-06-08) |
| S8 | [s8_phase6_observability.yaml](../../tests/stories/s8_phase6_observability.yaml) | Observability — agent.transition events published by all agents |
| S9 | [s9_multimodal.yaml](../../tests/stories/s9_multimodal.yaml) | Multimodal — image attachment passed to vision model |
| S10 | [s10_datetime_location.yaml](../../tests/stories/s10_datetime_location.yaml) | Date/time + location grounding — Gemma calls get_datetime and get_location instead of hallucinating |
| S11 | [s11_semantic_scholar.yaml](../../tests/stories/s11_semantic_scholar.yaml) | Academic search — Gemma calls search_papers for research questions |
| S12 | [s12_rag.yaml](../../tests/stories/s12_rag.yaml) | RAG library — Gemma calls search_library to query ingested documents |

## Story Structure

```yaml
story_id: S1
title: "Basic factual Q&A"
description: >
  What the story tests and why.

turns:
  - query: "User query text"
    expected_content:
      - "substring or regex that must appear in the answer"
    notes: "What makes this turn pass or fail"

expected_bus_events:
  present:
    - "response.generation"
    - "critique.result"
  absent:
    - "tool.request.web_search"   # should NOT be called for simple factual queries

notes: >
  Additional context, known edge cases, config requirements.
```

## Story Authoring Rules

- Assert **behavior** (correct answer content, correct bus events), not which tool fired for a given phrasing. Naming tools in the query text breaks loose coupling — Gemma should decide whether to call a tool based on its schema description, not the query wording.
- `expected_content` entries are substring matches (or regex). Use `|` for alternatives (e.g. `"Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday"`).
- Stories that require live services (SearXNG, Ollama) are marked in their `notes`. Unit tests in `tests/test_*.py` cover isolated component logic.
