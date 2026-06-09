from enum import Enum


class CriticState(Enum):
    IDLE = "idle"
    RECEIVING = "receiving"
    GRADING = "grading"
    PUBLISHING = "publishing"
    ERROR = "error"
