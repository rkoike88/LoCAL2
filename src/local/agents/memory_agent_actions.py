from enum import Enum


class MemoryAgentAction(Enum):
    START_INGEST = "start_ingest"
    COMPLETE = "complete"
    UPDATE_SCORE = "update_score"
