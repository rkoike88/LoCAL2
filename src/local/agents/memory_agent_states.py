from enum import Enum


class MemoryAgentState(Enum):
    IDLE = "idle"
    RETRIEVING = "retrieving"
    INGESTING = "ingesting"
    UPDATING_SCORE = "updating_score"
