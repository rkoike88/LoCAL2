# Implementation Plans

Phase-by-phase implementation plans. Each HTML file describes the design decisions, data flows, file changes, and out-of-scope items for that phase.

| Phase | Title | Plan doc |
|---|---|---|
| 1 | Generator core, web tools, dynamic schema registration | — |
| 2 | search_memory tool + MemoryAgent auto-ingest | — |
| 3 | CriticAgent absolute grading (1–5), UI score badge | — |
| 4 | Score-weighted retrieval, tool-skip on tool_calls, user thumbs → reward | — |
| 5 | Dual respondents (A/B), pairwise Prometheus, pairwise_winner annotation | — |
| 6 | Floating tool/agent windows, reactive spawn on tool.schema | — |
| 7 | AttachmentBar, clipboard paste, image/PDF handling | — |
| 8 | DateTimeTool + LocationTool, live IP geolocation | [plan_local2_phase8.html](../../.claude/plan_local2_phase8.html) |
| 9 | SemanticScholarTool, rate limiter, arXiv URL fallback | — |
| 10 | RAG library (search_library, DocumentService, DocumentsWindow) | — |
| 12 | Multi-collection RAG: collection in chunk ID, two-level UI, dynamic schema | [plan_local2_phase12.html](../../.claude/plan_local2_phase12.html) |
| 13 | Conversation session navigator (ConversationsWindow, rejoin, delete) | — |
| 14 | Context gauge (arc widget), token tracking, conversation compaction | [plan_local2_phase14.html](../../.claude/plan_local2_phase14.html) |
| 15 | GeneratorWindow: identity, state, context, tool registry, transitions | [plan_local2_phase15.html](../../.claude/plan_local2_phase15.html) |
