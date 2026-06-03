from enum import Enum


class CriticAction(Enum):
    RECEIVE = "receive"
    START_GRADE = "start_grade"
    START_PAIRWISE = "start_pairwise"
    PUBLISH = "publish"
    FAIL = "fail"
    RESET = "reset"
