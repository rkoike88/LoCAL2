"""CriticAgent transition table and StateMachine executor."""
from local.agents.critic_actions import CriticAction
from local.agents.critic_states import CriticState

TRANSITIONS: dict[tuple[CriticState, CriticAction], CriticState] = {
    (CriticState.IDLE,            CriticAction.RECEIVE):        CriticState.RECEIVING,
    (CriticState.RECEIVING,       CriticAction.START_GRADE):    CriticState.GRADING,
    (CriticState.GRADING,         CriticAction.PUBLISH):        CriticState.PUBLISHING,
    (CriticState.GRADING,         CriticAction.FAIL):           CriticState.ERROR,
    (CriticState.PUBLISHING,      CriticAction.RESET):          CriticState.IDLE,
    (CriticState.ERROR,           CriticAction.RESET):          CriticState.IDLE,
}


class CriticStateMachine:
    """Enforces the critic transition table. Raises on illegal transitions."""

    def __init__(self) -> None:
        self._state = CriticState.IDLE

    @property
    def state(self) -> CriticState:
        return self._state

    def transition(self, action: CriticAction) -> CriticState:
        key = (self._state, action)
        next_state = TRANSITIONS.get(key)
        if next_state is None:
            raise ValueError(
                f"Illegal transition: state={self._state.value!r} action={action.value!r}"
            )
        self._state = next_state
        return self._state
