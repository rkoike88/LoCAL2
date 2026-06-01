from enum import Enum, auto


class MemoryAgentAction(Enum):
    START_INGEST = auto()
    COMPLETE = auto()
