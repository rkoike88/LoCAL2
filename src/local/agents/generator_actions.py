"""GeneratorAgent action definitions."""

from enum import Enum


class GeneratorAction(Enum):
    RECEIVE = "receive"                 # query.received consumed
    START_GENERATION = "start_generation"   # begin ollama.chat()
    DISPATCH_TOOL = "dispatch_tool"     # tool_calls present, publishing tool.request.*
    AWAIT_RESULT = "await_result"       # tool.request.* published, blocking on tool.result.*
    TOOL_RESULT = "tool_result"         # tool.result.* received, resume loop
    TOOL_TIMEOUT = "tool_timeout"       # no tool.result.* within deadline, inject error
    PUBLISH = "publish"                 # no tool calls, answer ready
    RESET = "reset"                     # response.generation published, back to idle
    FAIL = "fail"                       # unrecoverable error
