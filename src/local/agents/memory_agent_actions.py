from enum import Enum


class MemoryAgentAction(Enum):
    START_RETRIEVE = "start_retrieve"
    START_INGEST = "start_ingest"
    COMPLETE = "complete"
    UPDATE_SCORE = "update_score"
