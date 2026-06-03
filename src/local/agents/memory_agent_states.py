from enum import Enum, auto


class MemoryAgentState(Enum):
    IDLE = auto()
    INGESTING = auto()
    UPDATING_SCORE = auto()
