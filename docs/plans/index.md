# Implementation Plans

Phase-by-phase implementation plans. Each HTML file describes the design decisions, data flows, file changes, and out-of-scope items for that phase.

| Phase | Title | Status | Plan doc |
|---|---|---|---|
| 1 | Generator core, web tools, dynamic schema registration | ✅ | [plan_local2.html](../../.claude/plan_local2.html) · [plan_local2_1b.html](../../.claude/plan_local2_1b.html) |
| 2 | search_memory tool + MemoryAgent auto-ingest | ✅ | [plan_local2_2.html](../../.claude/plan_local2_2.html) |
| 3 | CriticAgent absolute grading (1–5), UI score badge | ✅ | [plan_local2_3.html](../../.claude/plan_local2_3.html) |
| 4 | Score-weighted retrieval, tool-skip on tool_calls, user thumbs → reward | ✅ | — |
| 5 | ~~Dual respondents (A/B), pairwise Prometheus~~ — removed 2026-06-08; CriticAgent absolute grading only | ✅ → ↩ | [plan_local2_5.html](../../.claude/plan_local2_5.html) |
| 6 | Floating tool/agent windows, reactive spawn on tool.schema | ✅ | [plan_local2_6.html](../../.claude/plan_local2_6.html) |
| 7 | AttachmentBar, clipboard paste, image/PDF handling | ✅ | [plan_local2_phase7.html](../../.claude/plan_local2_phase7.html) |
| 8 | DateTimeTool + LocationTool, live IP geolocation | ✅ | [plan_local2_phase8.html](../../.claude/plan_local2_phase8.html) |
| 9 | SemanticScholarTool, rate limiter, arXiv URL fallback | ✅ | — |
| 10 | RAG library (search_library, DocumentService, DocumentsWindow) | ✅ | [plan_local2_phase10.html](../../.claude/plan_local2_phase10.html) |
| 11 | README, docs, PDF ingest fix, window tiling | ✅ | [plan_local2_plan2.html](../../.claude/plan_local2_plan2.html) |
| 12 | Multi-collection RAG: collection in chunk ID, two-level UI, dynamic schema | ✅ | [plan_local2_phase12.html](../../.claude/plan_local2_phase12.html) |
| 13 | Conversation session navigator (ConversationsWindow, rejoin, delete) | ✅ | [plan_local2_phase13.html](../../.claude/plan_local2_phase13.html) |
| 14 | Context gauge (arc widget), token tracking, conversation compaction | ✅ | [plan_local2_phase14.html](../../.claude/plan_local2_phase14.html) |
| 15 | GeneratorWindow: identity, state, context, tool registry, transitions | ✅ | [plan_local2_phase15.html](../../.claude/plan_local2_phase15.html) |
| — | Refactor: BaseTool + Google-style docstrings (commit 68976e0) | ✅ | — |
| — | Refactor: RespondentB removal + BaseAgent (commit dd5404e) | ✅ | — |
| 16 | Web UI: WebSocket gateway + React frontend replacing PySide6 | 📋 | [plan_local2_phase16.html](../../.claude/plan_local2_phase16.html) |

## Notes

**Phase 5 rollback:** The dual-respondent / pairwise comparison architecture was implemented and then removed in the 2026-06-08 refactor. `GeneratorAgent` no longer has an A/B identity. Pairwise comparison will be re-added at the `CriticAgent` layer when multiple independent generator processes are introduced (separate processes, matched by `correlation_id`).

**Refactor passes:** Two non-feature refactors were applied after Phase 15 — `BaseTool(ABC)` extracting common tool boilerplate, and `BaseAgent(ABC)` extracting common agent boilerplate (`_do_transition`, `run`, `_dispatch`).
