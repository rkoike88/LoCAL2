"""Subject constants for LoCAL2 envelopes."""

# Core conversation flow
QUERY_RECEIVED = "query.received"
GENERATION_THINKING = "generation.thinking"   # streaming thinking chunks
RESPONSE_GENERATION = "response.generation"
ANSWER_DIALOG = "answer.dialog"

# Tool bus — schema
TOOL_SCHEMA = "tool.schema"              # tools announce JSON schema on startup
TOOL_SCHEMA_REQUEST = "tool.schema.request"  # generator broadcasts on startup; tools re-announce

# Tool bus — calls (tool_dispatcher → tools) and results (tools → tool_dispatcher)
TOOL_CALL_WEB_SEARCH = "tool.call.web_search"
TOOL_RESULT_WEB_SEARCH = "tool.result.web_search"
TOOL_CALL_WEB_FETCH = "tool.call.web_fetch"
TOOL_RESULT_WEB_FETCH = "tool.result.web_fetch"
TOOL_CALL_SEARCH_MEMORY = "tool.call.search_memory"
TOOL_RESULT_SEARCH_MEMORY = "tool.result.search_memory"
TOOL_CALL_GET_DATETIME = "tool.call.get_datetime"
TOOL_RESULT_GET_DATETIME = "tool.result.get_datetime"
TOOL_CALL_GET_LOCATION = "tool.call.get_location"
TOOL_RESULT_GET_LOCATION = "tool.result.get_location"
TOOL_CALL_SEARCH_PAPERS = "tool.call.search_papers"
TOOL_RESULT_SEARCH_PAPERS = "tool.result.search_papers"
TOOL_CALL_SEARCH_DOCUMENTS = "tool.call.search_library"
TOOL_RESULT_SEARCH_DOCUMENTS = "tool.result.search_library"

# Tool activity — published by tools on every call/result cycle
TOOL_ACTIVITY_SEARCH_MEMORY = "tool.activity.search_memory"
TOOL_ACTIVITY_WEB_SEARCH = "tool.activity.web_search"
TOOL_ACTIVITY_WEB_FETCH = "tool.activity.web_fetch"
TOOL_ACTIVITY_GET_DATETIME = "tool.activity.get_datetime"
TOOL_ACTIVITY_GET_LOCATION = "tool.activity.get_location"
TOOL_ACTIVITY_SEARCH_PAPERS = "tool.activity.search_papers"
TOOL_ACTIVITY_SEARCH_DOCUMENTS = "tool.activity.search_library"

# Config hot-reload — UI publishes after saving a tool YAML; tool re-announces schema
CONFIG_RELOAD = "config.reload"

# Phase 2+
CRITIQUE       = "critique.result"
CRITIC_SKIPPED = "critic.skipped"
USER_FEEDBACK = "user.feedback"
REWARD_EVENT = "reward.event"

# Phase 6 — agent state transition visibility
AGENT_TRANSITION = "agent.transition"
TOOL_TRANSITION  = "tool.transition"

# Phase 14 — conversation compaction
COMPACTION_REQUEST = "compaction.request"
COMPACTION_RESULT  = "compaction.result"

# Phase 15 — generator observability
GENERATOR_STATUS = "generator.status"

# Persona tool
TOOL_CALL_PERSONA    = "tool.call.persona"
TOOL_RESULT_PERSONA  = "tool.result.persona"
TOOL_ACTIVITY_PERSONA = "tool.activity.persona"

TOOL_CALL_REMEMBER_THIS     = "tool.call.remember_this"
TOOL_RESULT_REMEMBER_THIS   = "tool.result.remember_this"
TOOL_ACTIVITY_REMEMBER_THIS = "tool.activity.remember_this"

# Phase 23 — structured context relay
MEMORY_CONTEXT       = "memory.context"
USER_CONTEXT_REQUEST = "user.context.request"
USER_CONTEXT         = "user.context"
USER_CONTEXT_UPDATED = "user.context.updated"

# Phase 21d — library agent tool
TOOL_CALL_CONSULT_LIBRARIAN   = "tool.call.consult_librarian"
TOOL_RESULT_CONSULT_LIBRARIAN = "tool.result.consult_librarian"
TOOL_ACTIVITY_CONSULT_LIBRARIAN = "tool.activity.consult_librarian"
LIBRARY_COLLECTION_CREATED    = "library.collection.created"
LIBRARY_INGEST_STARTED        = "library.ingest.started"
LIBRARY_INGEST_COMPLETE       = "library.ingest.complete"
