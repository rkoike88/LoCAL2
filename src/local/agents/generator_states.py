"""GeneratorAgent state definitions."""

from enum import Enum


class GeneratorState(Enum):
    IDLE = "idle"
    RECEIVING = "receiving"       # query.received consumed, building messages array
    GENERATING = "generating"     # ollama.chat() in flight
    DISPATCHING_TOOL = "dispatching_tool"   # publishing tool.request.* to bus
    WAITING_FOR_TOOL = "waiting_for_tool"   # blocking on tool.result.* from bus
    PUBLISHING = "publishing"     # publishing response.generation + answer.dialog
    ERROR = "error"
