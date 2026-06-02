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

# Memory tool bus — Phase 2 (4 single-purpose tools)
TOOL_REQUEST_SAVE_TOPIC = "tool.request.save_topic"
TOOL_RESULT_SAVE_TOPIC = "tool.result.save_topic"
TOOL_REQUEST_GET_TOPIC = "tool.request.get_topic"
TOOL_RESULT_GET_TOPIC = "tool.result.get_topic"
TOOL_REQUEST_SEARCH_MEMORY = "tool.request.search_memory"
TOOL_RESULT_SEARCH_MEMORY = "tool.result.search_memory"

# Tool activity — published by tools on every request/result cycle
TOOL_ACTIVITY_SAVE_TOPIC = "tool.activity.save_topic"
TOOL_ACTIVITY_GET_TOPIC = "tool.activity.get_topic"
TOOL_ACTIVITY_SEARCH_MEMORY = "tool.activity.search_memory"
TOOL_ACTIVITY_WEB_SEARCH = "tool.activity.web_search"
TOOL_ACTIVITY_WEB_FETCH = "tool.activity.web_fetch"

# Config hot-reload — UI publishes after saving a tool YAML; tool re-announces schema
CONFIG_RELOAD = "config.reload"

# Phase 2+
CRITIQUE = "critique.result"
USER_FEEDBACK = "user.feedback"
REWARD_EVENT = "reward.event"
