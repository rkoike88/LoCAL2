"""GeneratorAgent transition table and StateMachine executor."""

from __future__ import annotations

from local.agents.generator_actions import GeneratorAction
from local.agents.generator_states import GeneratorState

S = GeneratorState
A = GeneratorAction

GENERATOR_TRANSITIONS: dict[tuple[GeneratorState, GeneratorAction], GeneratorState] = {
    # Normal conversation flow
    (S.IDLE,              A.RECEIVE):         S.RECEIVING,
    (S.RECEIVING,         A.START_GENERATION):S.GENERATING,
    (S.GENERATING,        A.DISPATCH_TOOL):   S.DISPATCHING_TOOL,
    (S.GENERATING,        A.PUBLISH):         S.PUBLISHING,
    (S.DISPATCHING_TOOL,  A.AWAIT_RESULT):    S.WAITING_FOR_TOOL,
    (S.WAITING_FOR_TOOL,  A.TOOL_RESULT):     S.GENERATING,
    (S.WAITING_FOR_TOOL,  A.TOOL_TIMEOUT):    S.GENERATING,
    (S.PUBLISHING,        A.RESET):           S.IDLE,
    # Error recovery — every non-IDLE state can fail
    (S.ERROR,             A.RESET):           S.IDLE,
}

for _state in GeneratorState:
    if _state not in (GeneratorState.IDLE, GeneratorState.ERROR):
        GENERATOR_TRANSITIONS[(_state, A.FAIL)] = S.ERROR


class GeneratorStateMachine:
    """Enforces the generator transition table. Raises on illegal transitions."""

    def __init__(self) -> None:
        self._state = GeneratorState.IDLE

    @property
    def state(self) -> GeneratorState:
        return self._state

    def transition(self, action: GeneratorAction) -> GeneratorState:
        key = (self._state, action)
        next_state = GENERATOR_TRANSITIONS.get(key)
        if next_state is None:
            raise ValueError(
                f"Illegal transition: state={self._state.value!r} action={action.value!r}"
            )
        self._state = next_state
        return self._state

    def reset(self) -> None:
        """Force back to IDLE regardless of current state (use only on shutdown)."""
        self._state = GeneratorState.IDLE
