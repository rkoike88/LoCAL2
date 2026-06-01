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

# Memory tool bus — Phase 2
TOOL_REQUEST_RECALL_MEMORY = "tool.request.recall_memory"
TOOL_RESULT_RECALL_MEMORY = "tool.result.recall_memory"
TOOL_REQUEST_SAVE_MEMORY = "tool.request.save_memory"
TOOL_RESULT_SAVE_MEMORY = "tool.result.save_memory"

# Phase 2+
CRITIQUE = "critique.result"
USER_FEEDBACK = "user.feedback"
REWARD_EVENT = "reward.event"
