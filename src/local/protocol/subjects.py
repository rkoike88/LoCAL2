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

# Memory tool bus — Phase 2 (4 single-purpose tools)
TOOL_REQUEST_SAVE_TOPIC = "tool.request.save_topic"
TOOL_RESULT_SAVE_TOPIC = "tool.result.save_topic"
TOOL_REQUEST_RECALL_TOPIC = "tool.request.recall_topic"
TOOL_RESULT_RECALL_TOPIC = "tool.result.recall_topic"
TOOL_REQUEST_USER_INSTRUCTION_MEMORY = "tool.request.user_instruction_memory"
TOOL_RESULT_USER_INSTRUCTION_MEMORY = "tool.result.user_instruction_memory"
TOOL_REQUEST_SEARCH_MEMORY = "tool.request.search_memory"
TOOL_RESULT_SEARCH_MEMORY = "tool.result.search_memory"

# Phase 2+
CRITIQUE = "critique.result"
USER_FEEDBACK = "user.feedback"
REWARD_EVENT = "reward.event"
