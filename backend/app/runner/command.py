from __future__ import annotations

from collections.abc import Sequence

from app.models.schemas import ToolResult
from app.runner.agent import CancelToken, EventSink, default_agent_client, redact


def run_argv(
    tool_name: str,
    argv: Sequence[str],
    timeout: int = 10,
    *,
    on_event: EventSink | None = None,
    cancel_token: CancelToken | None = None,
) -> ToolResult:
    return default_agent_client().execute_argv(
        tool_name,
        argv,
        timeout=timeout,
        on_event=on_event,
        cancel_token=cancel_token,
    )


__all__ = ["CancelToken", "redact", "run_argv"]
