from __future__ import annotations

import json
import sys
import threading

import pytest

from app.runner.agent import AgentCommandRejected, CancelToken, CommandEvent, LocalAgentClient


def test_local_agent_rejects_shell_strings_and_redacts_streams() -> None:
    client = LocalAgentClient()
    events: list[CommandEvent] = []

    result = client.execute_argv(
        "test.redact",
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "print('safe line'); "
                "print('token=abc123'); "
                "print('authorization: bearer secret', file=sys.stderr)"
            ),
        ],
        on_event=events.append,
    )

    assert result.status.value == "success"
    assert "safe line" in result.data["stdout"]
    assert "token=abc123" not in result.data["output"]
    assert "token=abc123" not in json.dumps(result.data, ensure_ascii=False)
    assert "token=abc123" not in json.dumps(
        [event.payload for event in events], ensure_ascii=False
    )
    assert "authorization: bearer secret" not in result.data["output"]
    assert "[redacted secret-like line]" in result.data["output"]
    assert any(event.type == "stdout" and event.payload["line"] == "safe line" for event in events)
    assert any(event.type == "stderr" for event in events)

    with pytest.raises(AgentCommandRejected):
        client.execute_argv("test.rejected", "echo unsafe shell string")  # type: ignore[arg-type]


def test_local_agent_timeout_and_cancel() -> None:
    client = LocalAgentClient()

    timed_out = client.execute_argv(
        "test.timeout",
        [sys.executable, "-c", "import time; time.sleep(2)"],
        timeout=1,
    )
    assert timed_out.status.value == "error"
    assert timed_out.error == "timeout"
    assert timed_out.data["timed_out"] is True

    token = CancelToken()
    timer = threading.Timer(0.2, token.cancel)
    timer.start()
    cancelled = client.execute_argv(
        "test.cancel",
        [sys.executable, "-c", "import time; time.sleep(5)"],
        timeout=10,
        cancel_token=token,
    )
    timer.join(timeout=1)
    assert cancelled.status.value == "error"
    assert cancelled.error == "cancelled"
    assert cancelled.data["cancelled"] is True
