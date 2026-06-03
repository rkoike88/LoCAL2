"""Subject constants for LoCAL2 envelopes."""

# Core conversation flow
QUERY_RECEIVED = "query.received"
GENERATION_THINKING = "generation.thinking"   # streaming thinking chunks
RESPONSE_GENERATION = "response.generation"
ANSWER_DIALOG = "answer.dialog"

# Tool bus — Phase 1b
TOOL_SCHEMA = "tool.schema"              # tools announce JSON schema on startup
TOOL_REQUEST_WEB_SEARCH = "tool.request.web_search"
TOOL_RESULT_WEB_SEARCH = "tool.result.web_search"
TOOL_REQUEST_WEB_FETCH = "tool.request.web_fetch"
TOOL_RESULT_WEB_FETCH = "tool.result.web_fetch"

# Tool schema discovery
TOOL_SCHEMA_REQUEST = "schema.request"   # generator broadcasts on startup; tools re-announce

# Memory tool bus — Phase 2
TOOL_REQUEST_SEARCH_MEMORY = "tool.request.search_memory"
TOOL_RESULT_SEARCH_MEMORY = "tool.result.search_memory"

# Tool activity — published by tools on every request/result cycle
TOOL_ACTIVITY_SEARCH_MEMORY = "tool.activity.search_memory"
TOOL_ACTIVITY_WEB_SEARCH = "tool.activity.web_search"
TOOL_ACTIVITY_WEB_FETCH = "tool.activity.web_fetch"

# Config hot-reload — UI publishes after saving a tool YAML; tool re-announces schema
CONFIG_RELOAD = "config.reload"

# Phase 2+
CRITIQUE = "critique.result"
USER_FEEDBACK = "user.feedback"
REWARD_EVENT = "reward.event"

# Phase 5 — pairwise comparison
PAIRWISE_RESULT = "pairwise.result"

# Phase 6 — agent state transition visibility
AGENT_TRANSITION = "agent.transition"
