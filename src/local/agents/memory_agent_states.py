from enum import Enum


class MemoryAgentState(Enum):
    IDLE = "idle"
    INGESTING = "ingesting"
    UPDATING_SCORE = "updating_score"
