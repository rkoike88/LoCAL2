"""Typed message objects for the LoCAL2 bus.

Each class represents one bus subject. Publishers construct message objects
instead of raw dicts; consumers call from_envelope() to parse. The wire
format (JSON payload) is unchanged — to_envelope() produces the same dict
structure as the previous raw-dict publish calls.

ToolCall and ToolResult use a @property for subject because the subject
includes the tool name (e.g. tool.call.web_search).
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar

from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import (
    AGENT_TRANSITION,
    ANSWER_DIALOG,
    COMPACTION_REQUEST,
    COMPACTION_RESULT,
    CONFIG_RELOAD,
    CRITIQUE,
    GENERATION_THINKING,
    GENERATOR_STATUS,
    QUERY_RECEIVED,
    RESPONSE_GENERATION,
    REWARD_EVENT,
    TOOL_SCHEMA,
    TOOL_SCHEMA_REQUEST,
    USER_FEEDBACK,
)


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class BusMessage:
    """Base class for all typed bus messages.

    Subclasses declare subject and message_type as ClassVars. to_envelope()
    serialises to a MessageEnvelope for transport; from_envelope() parses
    one back. Both use lenient .get() defaults so partial payloads don't raise.
    """

    subject: ClassVar[str]
    message_type: ClassVar[str]

    def to_envelope(self, sender_id: str, correlation_id: str = "", session_id: str = "") -> MessageEnvelope:
        subject = self.subject if isinstance(self.subject, str) else type(self).subject
        return MessageEnvelope.create(
            message_type=type(self).message_type,
            subject=subject,
            sender_id=sender_id,
            payload=asdict(self),  # type: ignore[arg-type]
            correlation_id=correlation_id or str(uuid.uuid4()),
            metadata={"session_id": session_id},
        )

    @classmethod
    def from_envelope(cls, envelope: MessageEnvelope) -> "BusMessage":
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Core conversation flow
# ---------------------------------------------------------------------------

@dataclass
class QueryReceived(BusMessage):
    subject:      ClassVar[str] = QUERY_RECEIVED
    message_type: ClassVar[str] = "query"

    query:       str
    session_id:  str
    query_id:    str
    attachments: list = field(default_factory=list)

    @classmethod
    def from_envelope(cls, envelope: MessageEnvelope) -> "QueryReceived":
        p = envelope.payload
        return cls(
            query=p.get("query", ""),
            session_id=p.get("session_id", ""),
            query_id=p.get("query_id", ""),
            attachments=p.get("attachments") or [],
        )


@dataclass
class GenerationThinking(BusMessage):
    subject:      ClassVar[str] = GENERATION_THINKING
    message_type: ClassVar[str] = "thinking"

    chunk:      str
    session_id: str
    query_id:   str

    @classmethod
    def from_envelope(cls, envelope: MessageEnvelope) -> "GenerationThinking":
        p = envelope.payload
        return cls(
            chunk=p.get("chunk", ""),
            session_id=p.get("session_id", ""),
            query_id=p.get("query_id", ""),
        )


@dataclass
class ResponseGeneration(BusMessage):
    subject:      ClassVar[str] = RESPONSE_GENERATION
    message_type: ClassVar[str] = "response"

    query:         str
    answer:        str
    thinking:      str
    tool_calls:    list
    session_id:    str
    query_id:      str
    prompt_tokens: int = 0
    error:         bool = False

    @classmethod
    def from_envelope(cls, envelope: MessageEnvelope) -> "ResponseGeneration":
        p = envelope.payload
        return cls(
            query=p.get("query", ""),
            answer=p.get("answer", ""),
            thinking=p.get("thinking", ""),
            tool_calls=p.get("tool_calls") or [],
            session_id=p.get("session_id", ""),
            query_id=p.get("query_id", ""),
            prompt_tokens=p.get("prompt_tokens", 0),
            error=bool(p.get("error", False)),
        )


@dataclass
class AnswerDialog(BusMessage):
    subject:      ClassVar[str] = ANSWER_DIALOG
    message_type: ClassVar[str] = "dialog"

    query:      str
    answer:     str
    session_id: str
    query_id:   str

    @classmethod
    def from_envelope(cls, envelope: MessageEnvelope) -> "AnswerDialog":
        p = envelope.payload
        return cls(
            query=p.get("query", ""),
            answer=p.get("answer", ""),
            session_id=p.get("session_id", ""),
            query_id=p.get("query_id", ""),
        )


# ---------------------------------------------------------------------------
# Tool schema
# ---------------------------------------------------------------------------

@dataclass
class ToolSchema(BusMessage):
    subject:      ClassVar[str] = TOOL_SCHEMA
    message_type: ClassVar[str] = "tool_schema"

    schema: dict = field(default_factory=dict)

    @classmethod
    def from_envelope(cls, envelope: MessageEnvelope) -> "ToolSchema":
        return cls(schema=envelope.payload.get("schema") or {})


@dataclass
class ToolSchemaRequest(BusMessage):
    subject:      ClassVar[str] = TOOL_SCHEMA_REQUEST
    message_type: ClassVar[str] = "tool_schema_request"

    @classmethod
    def from_envelope(cls, envelope: MessageEnvelope) -> "ToolSchemaRequest":
        return cls()

    def to_envelope(self, sender_id: str, correlation_id: str = "", session_id: str = "") -> MessageEnvelope:
        return MessageEnvelope.create(
            message_type=self.message_type,
            subject=self.subject,
            sender_id=sender_id,
            payload={},
            correlation_id=correlation_id or str(uuid.uuid4()),
        )


# ---------------------------------------------------------------------------
# Tool call / result — subject includes tool name; use @property
# ---------------------------------------------------------------------------

@dataclass
class ToolCall(BusMessage):
    message_type: ClassVar[str] = "tool_call"

    tool:           str
    args:           dict
    correlation_id: str = ""

    @property  # type: ignore[override]
    def subject(self) -> str:  # type: ignore[override]
        return f"tool.call.{self.tool}"

    def to_envelope(self, sender_id: str, correlation_id: str = "", session_id: str = "") -> MessageEnvelope:
        cid = correlation_id or self.correlation_id or str(uuid.uuid4())
        return MessageEnvelope.create(
            message_type=self.message_type,
            subject=self.subject,
            sender_id=sender_id,
            payload={"tool": self.tool, "args": self.args},
            correlation_id=cid,
        )

    @classmethod
    def from_envelope(cls, envelope: MessageEnvelope) -> "ToolCall":
        p = envelope.payload
        return cls(
            tool=p.get("tool", ""),
            args=p.get("args") or {},
            correlation_id=envelope.correlation_id or "",
        )


@dataclass
class ToolResult(BusMessage):
    message_type: ClassVar[str] = "tool_result"

    tool:           str
    result:         str
    correlation_id: str = ""
    sources:        list = field(default_factory=list)

    @property  # type: ignore[override]
    def subject(self) -> str:  # type: ignore[override]
        return f"tool.result.{self.tool}"

    def to_envelope(self, sender_id: str, correlation_id: str = "", session_id: str = "") -> MessageEnvelope:
        cid = correlation_id or self.correlation_id or str(uuid.uuid4())
        return MessageEnvelope.create(
            message_type=self.message_type,
            subject=self.subject,
            sender_id=sender_id,
            payload={"tool": self.tool, "result": self.result, "sources": self.sources},
            correlation_id=cid,
        )

    @classmethod
    def from_envelope(cls, envelope: MessageEnvelope) -> "ToolResult":
        p = envelope.payload
        return cls(
            tool=p.get("tool", ""),
            result=p.get("result", ""),
            correlation_id=envelope.correlation_id or "",
        )


@dataclass
class ToolActivity(BusMessage):
    message_type: ClassVar[str] = "tool_activity"

    tool:    str
    event:   str        # "request" or "result"
    data:    dict = field(default_factory=dict)

    @property  # type: ignore[override]
    def subject(self) -> str:  # type: ignore[override]
        return f"tool.activity.{self.tool}"

    def to_envelope(self, sender_id: str, correlation_id: str = "", session_id: str = "") -> MessageEnvelope:
        return MessageEnvelope.create(
            message_type=self.message_type,
            subject=self.subject,
            sender_id=sender_id,
            payload={"event": self.event, "tool": self.tool, **self.data},
            correlation_id=correlation_id or str(uuid.uuid4()),
        )

    @classmethod
    def from_envelope(cls, envelope: MessageEnvelope) -> "ToolActivity":
        p = envelope.payload
        tool = p.get("tool", "")
        event = p.get("event", "")
        data = {k: v for k, v in p.items() if k not in ("tool", "event")}
        return cls(tool=tool, event=event, data=data)


# ---------------------------------------------------------------------------
# Critic
# ---------------------------------------------------------------------------

@dataclass
class CritiqueResult(BusMessage):
    subject:      ClassVar[str] = CRITIQUE
    message_type: ClassVar[str] = "critique"

    score:      Any     # int 1-5 or None
    feedback:   str
    query:      str
    answer:     str
    session_id: str
    query_id:   str

    @classmethod
    def from_envelope(cls, envelope: MessageEnvelope) -> "CritiqueResult":
        p = envelope.payload
        return cls(
            score=p.get("score"),
            feedback=p.get("feedback", ""),
            query=p.get("query", ""),
            answer=p.get("answer", ""),
            session_id=p.get("session_id", ""),
            query_id=p.get("query_id", ""),
        )


# ---------------------------------------------------------------------------
# Feedback / reward
# ---------------------------------------------------------------------------

@dataclass
class UserFeedback(BusMessage):
    subject:      ClassVar[str] = USER_FEEDBACK
    message_type: ClassVar[str] = "user_feedback"

    query_id:   str
    session_id: str
    sentiment:  str     # "positive" or "negative"

    @classmethod
    def from_envelope(cls, envelope: MessageEnvelope) -> "UserFeedback":
        p = envelope.payload
        return cls(
            query_id=p.get("query_id", ""),
            session_id=p.get("session_id", ""),
            sentiment=p.get("sentiment", ""),
        )


@dataclass
class RewardEvent(BusMessage):
    subject:      ClassVar[str] = REWARD_EVENT
    message_type: ClassVar[str] = "reward"

    query_id:   str
    session_id: str
    sentiment:  str

    @classmethod
    def from_envelope(cls, envelope: MessageEnvelope) -> "RewardEvent":
        p = envelope.payload
        return cls(
            query_id=p.get("query_id", ""),
            session_id=p.get("session_id", ""),
            sentiment=p.get("sentiment", ""),
        )


# ---------------------------------------------------------------------------
# Agent state transitions
# ---------------------------------------------------------------------------

@dataclass
class AgentTransition(BusMessage):
    subject:      ClassVar[str] = AGENT_TRANSITION
    message_type: ClassVar[str] = "agent_transition"

    agent:  str
    from_state: str = field(metadata={"key": "from"})
    action: str
    to:     str

    def to_envelope(self, sender_id: str, correlation_id: str = "", session_id: str = "") -> MessageEnvelope:
        return MessageEnvelope.create(
            message_type=self.message_type,
            subject=self.subject,
            sender_id=sender_id,
            payload={
                "agent":  self.agent,
                "from":   self.from_state,
                "action": self.action,
                "to":     self.to,
            },
            correlation_id=correlation_id or str(uuid.uuid4()),
        )

    @classmethod
    def from_envelope(cls, envelope: MessageEnvelope) -> "AgentTransition":
        p = envelope.payload
        return cls(
            agent=p.get("agent", ""),
            from_state=p.get("from", ""),
            action=p.get("action", ""),
            to=p.get("to", ""),
        )


# ---------------------------------------------------------------------------
# Compaction
# ---------------------------------------------------------------------------

@dataclass
class CompactionRequest(BusMessage):
    subject:      ClassVar[str] = COMPACTION_REQUEST
    message_type: ClassVar[str] = "compaction_request"

    session_id: str
    auto:       bool = False

    @classmethod
    def from_envelope(cls, envelope: MessageEnvelope) -> "CompactionRequest":
        p = envelope.payload
        return cls(
            session_id=p.get("session_id", ""),
            auto=bool(p.get("auto", False)),
        )


@dataclass
class CompactionResult(BusMessage):
    subject:      ClassVar[str] = COMPACTION_RESULT
    message_type: ClassVar[str] = "compaction_result"

    session_id:       str
    tokens_before:    int = 0
    tokens_after:     int = 0
    summary:          str = ""
    error:            str = ""

    @classmethod
    def from_envelope(cls, envelope: MessageEnvelope) -> "CompactionResult":
        p = envelope.payload
        return cls(
            session_id=p.get("session_id", ""),
            tokens_before=p.get("tokens_before", 0),
            tokens_after=p.get("tokens_after", 0),
            summary=p.get("summary", ""),
            error=p.get("error", ""),
        )


# ---------------------------------------------------------------------------
# Generator status
# ---------------------------------------------------------------------------

@dataclass
class GeneratorStatus(BusMessage):
    subject:      ClassVar[str] = GENERATOR_STATUS
    message_type: ClassVar[str] = "generator_status"

    instance_id:   str
    model:         str
    temperature:   float
    num_ctx:       int
    state:         str
    token_count:   int
    tool_names:    list
    system_prompt: str

    @classmethod
    def from_envelope(cls, envelope: MessageEnvelope) -> "GeneratorStatus":
        p = envelope.payload
        return cls(
            instance_id=p.get("instance_id", ""),
            model=p.get("model", ""),
            temperature=p.get("temperature", 0.0),
            num_ctx=p.get("num_ctx", 0),
            state=p.get("state", ""),
            token_count=p.get("token_count", 0),
            tool_names=p.get("tool_names") or [],
            system_prompt=p.get("system_prompt", ""),
        )


# ---------------------------------------------------------------------------
# Config reload
# ---------------------------------------------------------------------------

@dataclass
class ConfigReload(BusMessage):
    subject:      ClassVar[str] = CONFIG_RELOAD
    message_type: ClassVar[str] = "config_reload"

    target: str = ""

    @classmethod
    def from_envelope(cls, envelope: MessageEnvelope) -> "ConfigReload":
        return cls(target=envelope.payload.get("target", ""))
