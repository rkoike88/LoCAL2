from local.agents.memory_agent_actions import MemoryAgentAction
from local.agents.memory_agent_states import MemoryAgentState

TRANSITIONS: dict[tuple[MemoryAgentState, MemoryAgentAction], MemoryAgentState] = {
    (MemoryAgentState.IDLE,      MemoryAgentAction.START_INGEST): MemoryAgentState.INGESTING,
    (MemoryAgentState.INGESTING, MemoryAgentAction.COMPLETE):     MemoryAgentState.IDLE,
}
