from enum import Enum


class CriticState(Enum):
    IDLE = "idle"
    RECEIVING = "receiving"
    GRADING = "grading"
    PAIRWISE_GRADING = "pairwise_grading"
    PUBLISHING = "publishing"
    ERROR = "error"
