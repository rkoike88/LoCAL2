from enum import Enum


class MemoryAgentState(Enum):
    IDLE = "idle"
    INGESTING = "ingesting"
    UPDATING_SCORE = "updating_score"
    ANNOTATING_PAIRWISE = "annotating_pairwise"
