from local.agents.memory_agent_actions import MemoryAgentAction
from local.agents.memory_agent_states import MemoryAgentState

TRANSITIONS: dict[tuple[MemoryAgentState, MemoryAgentAction], MemoryAgentState] = {
    (MemoryAgentState.IDLE,                MemoryAgentAction.START_INGEST):      MemoryAgentState.INGESTING,
    (MemoryAgentState.INGESTING,           MemoryAgentAction.COMPLETE):          MemoryAgentState.IDLE,
    (MemoryAgentState.IDLE,                MemoryAgentAction.UPDATE_SCORE):      MemoryAgentState.UPDATING_SCORE,
    (MemoryAgentState.UPDATING_SCORE,      MemoryAgentAction.COMPLETE):          MemoryAgentState.IDLE,
    (MemoryAgentState.IDLE,                MemoryAgentAction.ANNOTATE_PAIRWISE): MemoryAgentState.ANNOTATING_PAIRWISE,
    (MemoryAgentState.ANNOTATING_PAIRWISE, MemoryAgentAction.COMPLETE):          MemoryAgentState.IDLE,
}


class MemoryAgentStateMachine:
    """Enforces the memory agent transition table. Raises on illegal transitions."""

    def __init__(self) -> None:
        self._state = MemoryAgentState.IDLE

    @property
    def state(self) -> MemoryAgentState:
        return self._state

    def transition(self, action: MemoryAgentAction) -> MemoryAgentState:
        key = (self._state, action)
        next_state = TRANSITIONS.get(key)
        if next_state is None:
            raise ValueError(
                f"Illegal transition: state={self._state.value!r} action={action.value!r}"
            )
        self._state = next_state
        return self._state
