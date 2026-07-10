from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import Protocol

from app.models.schemas import ToolResult, ToolStatus


MAX_OUTPUT = 8_000
SECRET_MARKERS = ("password=", "token=", "api_key=", "secret=", "authorization:", "bearer ")


def redact(text: str) -> str:
    redacted_lines: list[str] = []
    for line in text.splitlines():
        lower = line.lower()
        if any(marker in lower for marker in SECRET_MARKERS):
            redacted_lines.append("[redacted secret-like line]")
        else:
            redacted_lines.append(line)
    return "\n".join(redacted_lines)


@dataclass
class CommandEvent:
    type: str
    payload: dict
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class CancelToken:
    def __init__(self) -> None:
        self._requested = False

    @property
    def requested(self) -> bool:
        return self._requested

    def cancel(self) -> None:
        self._requested = True


class AgentCommandRejected(ValueError):
    pass


EventSink = Callable[[CommandEvent], None]


class AgentClient(Protocol):
    agent_id: str
    transport: str

    def execute_argv(
        self,
        tool_name: str,
        argv: Sequence[str],
        *,
        timeout: int = 10,
        on_event: EventSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ToolResult:
        ...


class LocalAgentClient:
    agent_id = "agent_local"
    transport = "in_process"

    def execute_argv(
        self,
        tool_name: str,
        argv: Sequence[str],
        *,
        timeout: int = 10,
        on_event: EventSink | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ToolResult:
        started = datetime.now(UTC)
        begin = monotonic()
        normalized = _normalize_argv(argv)
        reported_argv = _redact_argv(normalized)
        if cancel_token and cancel_token.requested:
            return self._result(
                tool_name,
                started,
                begin,
                ToolStatus.ERROR,
                "command cancelled before start",
                reported_argv,
                error="cancelled",
                extra={"cancelled": True},
            )

        if shutil.which(normalized[0]) is None:
            return self._result(
                tool_name,
                started,
                begin,
                ToolStatus.NO_DATA,
                f"{normalized[0]} is not available on this host",
                reported_argv,
            )

        _emit(on_event, "command_started", {"argv": reported_argv, "agent_id": self.agent_id})
        try:
            process = subprocess.Popen(
                normalized,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            return self._result(
                tool_name,
                started,
                begin,
                ToolStatus.ERROR,
                f"failed to start command: {exc}",
                reported_argv,
                error=str(exc),
            )

        deadline = monotonic() + max(timeout, 1)
        stdout = ""
        stderr = ""
        while True:
            if cancel_token and cancel_token.requested:
                _terminate(process)
                _emit(on_event, "command_cancelled", {"argv": reported_argv})
                return self._result(
                    tool_name,
                    started,
                    begin,
                    ToolStatus.ERROR,
                    "command cancelled",
                    reported_argv,
                    error="cancelled",
                    extra={"cancelled": True},
                )

            remaining = deadline - monotonic()
            if remaining <= 0:
                _terminate(process)
                _emit(on_event, "command_timeout", {"argv": reported_argv, "timeout": timeout})
                return self._result(
                    tool_name,
                    started,
                    begin,
                    ToolStatus.ERROR,
                    f"command timed out after {timeout}s",
                    reported_argv,
                    error="timeout",
                    extra={"timed_out": True},
                )

            try:
                stdout, stderr = process.communicate(timeout=min(0.1, remaining))
                break
            except subprocess.TimeoutExpired:
                continue

        stdout = redact(stdout or "")
        stderr = redact(stderr or "")
        _emit_lines(on_event, "stdout", stdout)
        _emit_lines(on_event, "stderr", stderr)
        output = _truncate("\n".join(part for part in (stdout, stderr) if part))
        status = ToolStatus.SUCCESS if process.returncode == 0 else ToolStatus.ERROR
        if not output.strip() and process.returncode == 0:
            status = ToolStatus.NO_DATA
        first_line = output.strip().splitlines()[0] if output.strip() else "no output"
        _emit(
            on_event,
            "command_finished",
            {"argv": reported_argv, "return_code": process.returncode, "status": status.value},
        )
        return self._result(
            tool_name,
            started,
            begin,
            status,
            first_line,
            reported_argv,
            error=None if process.returncode == 0 else f"exit code {process.returncode}",
            extra={
                "stdout": stdout,
                "stderr": stderr,
                "output": output,
                "return_code": process.returncode,
            },
        )

    def _result(
        self,
        tool_name: str,
        started: datetime,
        begin: float,
        status: ToolStatus,
        summary: str,
        argv: list[str],
        *,
        error: str | None = None,
        extra: dict | None = None,
    ) -> ToolResult:
        data = {
            "argv": argv,
            "agent_id": self.agent_id,
            "agent_transport": self.transport,
            "shell": False,
        }
        if extra:
            data.update(extra)
        return ToolResult(
            tool_name=tool_name,
            status=status,
            started_at=started,
            duration_ms=int((monotonic() - begin) * 1000),
            output_summary=summary,
            error=error,
            data=data,
        )


_default_agent_client: AgentClient = LocalAgentClient()


def default_agent_client() -> AgentClient:
    return _default_agent_client


def set_default_agent_client(client: AgentClient) -> None:
    global _default_agent_client
    _default_agent_client = client


def _normalize_argv(argv: Sequence[str]) -> list[str]:
    if isinstance(argv, str):
        raise AgentCommandRejected("shell command strings are not accepted; pass argv instead")
    normalized = [str(part) for part in argv if str(part)]
    if not normalized:
        raise AgentCommandRejected("argv must include an executable")
    if any("\x00" in part for part in normalized):
        raise AgentCommandRejected("argv must not contain null bytes")
    return normalized


def _redact_argv(argv: Sequence[str]) -> list[str]:
    reported: list[str] = []
    for part in argv:
        lower = part.lower()
        if any(marker in lower for marker in SECRET_MARKERS):
            reported.append("[redacted secret-like arg]")
        else:
            reported.append(part)
    return reported


def _emit(on_event: EventSink | None, event_type: str, payload: dict) -> None:
    if on_event:
        on_event(CommandEvent(event_type, _redact_payload(payload)))


def _redact_payload(payload: dict) -> dict:
    return {key: _redact_value(value) for key, value in payload.items()}


def _redact_value(value):
    if isinstance(value, str):
        lower = value.lower()
        if any(marker in lower for marker in SECRET_MARKERS):
            return "[redacted secret-like value]"
        return value
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    return value


def _emit_lines(on_event: EventSink | None, event_type: str, text: str) -> None:
    for line in text.splitlines():
        _emit(on_event, event_type, {"line": line})


def _terminate(process: subprocess.Popen[str]) -> None:
    process.terminate()
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1)


def _truncate(output: str) -> str:
    if len(output) <= MAX_OUTPUT:
        return output
    return output[:MAX_OUTPUT] + "\n[truncated]"
