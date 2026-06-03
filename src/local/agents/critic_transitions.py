from local.agents.critic_actions import CriticAction
from local.agents.critic_states import CriticState

TRANSITIONS: dict[tuple[CriticState, CriticAction], CriticState] = {
    (CriticState.IDLE,       CriticAction.RECEIVE):     CriticState.RECEIVING,
    (CriticState.RECEIVING,  CriticAction.START_GRADE): CriticState.GRADING,
    (CriticState.GRADING,    CriticAction.PUBLISH):     CriticState.PUBLISHING,
    (CriticState.GRADING,    CriticAction.FAIL):        CriticState.ERROR,
    (CriticState.PUBLISHING, CriticAction.RESET):       CriticState.IDLE,
    (CriticState.ERROR,      CriticAction.RESET):       CriticState.IDLE,
}
