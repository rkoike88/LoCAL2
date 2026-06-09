from enum import Enum


class CriticAction(Enum):
    RECEIVE = "receive"
    START_GRADE = "start_grade"
    PUBLISH = "publish"
    FAIL = "fail"
    RESET = "reset"
