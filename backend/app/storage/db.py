from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from hashlib import pbkdf2_hmac
from hmac import compare_digest
from pathlib import Path
from secrets import token_urlsafe
from typing import Any, Callable, Iterator


DB_PATH = Path(os.getenv("AIOPS_DB_PATH", "data/dev.sqlite"))
BOOTSTRAP_PATH = Path("data/bootstrap_password.txt")


def configure(db_path: Path, bootstrap_path: Path) -> None:
    global DB_PATH, BOOTSTRAP_PATH
    DB_PATH = db_path
    BOOTSTRAP_PATH = bootstrap_path


def now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
    except BaseException:
        con.rollback()
        raise
    else:
        con.commit()
    finally:
        con.close()


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or token_urlsafe(16)
    digest = pbkdf2_hmac("sha256", password.encode(), salt.encode(), 210_000).hex()
    return f"pbkdf2_sha256${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, salt, digest = stored.split("$", 2)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    candidate = hash_password(password, salt).split("$", 2)[2]
    return compare_digest(candidate, digest)


def init_db(*, create_bootstrap_admin: bool | None = None) -> None:
    if create_bootstrap_admin is None:
        create_bootstrap_admin = os.getenv("AIOPS_ENV", "dev").strip().lower() != "prod"
    with connect() as con:
        con.executescript(
            """
            create table if not exists users (
              id text primary key,
              username text unique not null,
              password_hash text not null,
              role text not null,
              must_change_password integer not null,
              created_at text not null
            );
            create table if not exists sessions (
              id text primary key,
              actor_id text not null,
              created_at text not null,
              expires_at text not null,
              revoked integer not null default 0
            );
            create table if not exists audit_records (
              id text primary key,
              actor_id text not null,
              event_type text not null,
              resource text not null,
              risk_level text not null,
              status text not null,
              summary text not null,
              created_at text not null
            );
            create table if not exists setup_state (
              id text primary key check (id = 'initial_setup'),
              consumed_at text not null,
              admin_user_id text not null,
              audit_id text not null
            );
            create table if not exists tool_results (
              id text primary key,
              actor_id text not null,
              payload text not null,
              created_at text not null
            );
            create table if not exists diagnosis_sessions (
              id text primary key,
              actor_id text not null,
              status text not null,
              user_question text not null,
              created_at text not null,
              updated_at text not null
            );
            create table if not exists diagnosis_events (
              id text primary key,
              session_id text not null,
              sequence integer not null,
              type text not null,
              payload text not null,
              created_at text not null
            );
            create table if not exists approvals (
              id text primary key,
              auth_session_id text not null,
              diagnosis_session_id text,
              actor_id text not null,
              nonce text not null,
              action_fingerprint text not null,
              tool_name text not null,
              target text not null,
              canonical_params text not null,
              risk_level text not null,
              expires_at text not null,
              status text not null
            );
            create table if not exists reports (
              id text primary key,
              payload text not null,
              created_at text not null
            );
            create table if not exists ops_tasks (
              id text primary key,
              type text not null,
              title text not null,
              status text not null,
              actor_id text not null,
              resource text not null,
              risk_level text not null,
              reason text not null,
              created_at text not null,
              started_at text,
              finished_at text,
              cancel_requested integer not null default 0,
              failure_reason text,
              approval_id text
            );
            create table if not exists ops_subtasks (
              id text primary key,
              task_id text not null,
              title text not null,
              status text not null,
              sequence integer not null,
              started_at text,
              finished_at text,
              rollback_step integer not null default 0,
              output_ref text,
              failure_reason text
            );
            create table if not exists ops_events (
              id text primary key,
              scope text not null,
              scope_id text not null,
              sequence integer not null,
              type text not null,
              payload text not null,
              created_at text not null
            );
            """
        )
        user = con.execute("select id from users where username = ?", ("admin",)).fetchone()
        if user is None and create_bootstrap_admin:
            password = os.getenv("AIOPS_BOOTSTRAP_ADMIN_PASSWORD")
            if not password:
                password = token_urlsafe(14)
                BOOTSTRAP_PATH.parent.mkdir(parents=True, exist_ok=True)
                BOOTSTRAP_PATH.write_text(password + "\n", encoding="utf-8")
            con.execute(
                "insert into users values (?, ?, ?, ?, ?, ?)",
                (
                    "user_admin",
                    "admin",
                    hash_password(password),
                    "admin",
                    1,
                    now().isoformat(),
                ),
            )


def ping() -> bool:
    try:
        with connect() as con:
            row = con.execute("select 1 as ok").fetchone()
        return bool(row and row["ok"] == 1)
    except sqlite3.Error:
        return False


def has_users() -> bool:
    try:
        with connect() as con:
            row = con.execute("select 1 from users limit 1").fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def setup_consumed() -> bool:
    try:
        with connect() as con:
            row = con.execute(
                "select 1 from setup_state where id = 'initial_setup'"
            ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def complete_initial_setup(
    username: str,
    password: str,
    *,
    failure_hook: Callable[[str], None] | None = None,
) -> bool:
    """Atomically create the first admin, consume setup, and record its audit event."""
    user_id = "user_admin" if username == "admin" else new_id("user")
    audit_id = new_id("audit")
    created_at = now().isoformat()
    with connect() as con:
        con.execute("begin immediate")
        if (
            con.execute("select 1 from users limit 1").fetchone() is not None
            or con.execute(
                "select 1 from setup_state where id = 'initial_setup'"
            ).fetchone()
            is not None
        ):
            return False
        con.execute(
            "insert into users values (?, ?, ?, ?, ?, ?)",
            (
                user_id,
                username,
                hash_password(password),
                "admin",
                0,
                created_at,
            ),
        )
        if failure_hook:
            failure_hook("after_admin")
        con.execute(
            "insert into setup_state values ('initial_setup', ?, ?, ?)",
            (created_at, user_id, audit_id),
        )
        if failure_hook:
            failure_hook("after_marker")
        con.execute(
            "insert into audit_records values (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                audit_id,
                "system_setup",
                "setup_enrollment_completed",
                "initial-admin",
                "mutating",
                "succeeded",
                "Production setup enrollment completed",
                created_at,
            ),
        )
        if failure_hook:
            failure_hook("after_audit")
    return True


def get_user(username: str) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute("select * from users where username = ?", (username,)).fetchone()


def get_user_by_id(user_id: str) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute("select * from users where id = ?", (user_id,)).fetchone()


def set_password(user_id: str, password: str) -> None:
    with connect() as con:
        con.execute(
            "update users set password_hash = ?, must_change_password = 0 where id = ?",
            (hash_password(password), user_id),
        )


def create_session(actor_id: str) -> dict[str, Any]:
    created_at = now()
    expires_at = created_at + timedelta(hours=8)
    session_id = new_id("sess")
    with connect() as con:
        con.execute(
            "insert into sessions values (?, ?, ?, ?, 0)",
            (session_id, actor_id, created_at.isoformat(), expires_at.isoformat()),
        )
    return {
        "session_id": session_id,
        "actor_id": actor_id,
        "created_at": created_at,
        "expires_at": expires_at,
    }


def get_session(session_id: str) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            "select * from sessions where id = ? and revoked = 0", (session_id,)
        ).fetchone()


def revoke_session(session_id: str) -> None:
    with connect() as con:
        con.execute("update sessions set revoked = 1 where id = ?", (session_id,))


def insert_audit(record: dict[str, Any]) -> None:
    with connect() as con:
        con.execute(
            "insert into audit_records values (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record["id"],
                record["actor_id"],
                record["event_type"],
                record["resource"],
                record["risk_level"],
                record["status"],
                record["summary"],
                record["created_at"],
            ),
        )


def list_audit(limit: int = 100) -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            "select * from audit_records order by created_at desc limit ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


def store_tool_result(actor_id: str, payload: dict[str, Any]) -> str:
    result_id = new_id("toolres")
    with connect() as con:
        con.execute(
            "insert into tool_results values (?, ?, ?, ?)",
            (result_id, actor_id, json.dumps(payload), now().isoformat()),
        )
    return f"audit://tool-results/{result_id}"


def create_diagnosis(actor_id: str, question: str) -> dict[str, Any]:
    current = now().isoformat()
    session_id = new_id("diag")
    with connect() as con:
        con.execute(
            "insert into diagnosis_sessions values (?, ?, ?, ?, ?, ?)",
            (session_id, actor_id, "running", question, current, current),
        )
    return {
        "id": session_id,
        "status": "running",
        "user_question": question,
        "created_at": current,
        "updated_at": current,
    }


def finish_diagnosis(session_id: str, status: str = "completed") -> None:
    with connect() as con:
        con.execute(
            "update diagnosis_sessions set status = ?, updated_at = ? where id = ?",
            (status, now().isoformat(), session_id),
        )


def add_event(session_id: str, sequence: int, event_type: str, payload: dict[str, Any]) -> None:
    with connect() as con:
        con.execute(
            "insert into diagnosis_events values (?, ?, ?, ?, ?, ?)",
            (
                new_id("evt"),
                session_id,
                sequence,
                event_type,
                json.dumps(payload),
                now().isoformat(),
            ),
        )


def list_events(session_id: str) -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            "select * from diagnosis_events where session_id = ? order by sequence asc",
            (session_id,),
        ).fetchall()
    return [
        {
            "type": row["type"],
            "session_id": row["session_id"],
            "sequence": row["sequence"],
            "payload": json.loads(row["payload"]),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def create_approval(payload: dict[str, Any]) -> None:
    with connect() as con:
        con.execute(
            "insert into approvals values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                payload["id"],
                payload["auth_session_id"],
                payload.get("diagnosis_session_id"),
                payload["actor_id"],
                payload["nonce"],
                payload["action_fingerprint"],
                payload["tool_name"],
                payload["target"],
                json.dumps(payload["canonical_params"], sort_keys=True),
                payload["risk_level"],
                payload["expires_at"],
                payload["status"],
            ),
        )


def get_approval(approval_id: str) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute("select * from approvals where id = ?", (approval_id,)).fetchone()


def update_approval_status(approval_id: str, status: str) -> None:
    with connect() as con:
        con.execute("update approvals set status = ? where id = ?", (status, approval_id))


def transition_approval_status(approval_id: str, from_status: str, to_status: str) -> bool:
    with connect() as con:
        cursor = con.execute(
            "update approvals set status = ? where id = ? and status = ?",
            (to_status, approval_id, from_status),
        )
        return cursor.rowcount == 1


def create_report(payload: dict[str, Any]) -> None:
    with connect() as con:
        con.execute(
            "insert into reports values (?, ?, ?)",
            (payload["id"], json.dumps(payload), payload["created_at"]),
        )


def list_reports() -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute("select payload from reports order by created_at desc").fetchall()
    return [json.loads(row["payload"]) for row in rows]


def _task_from_row(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    payload["cancel_requested"] = bool(payload["cancel_requested"])
    return payload


def create_ops_task(actor_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    current = now().isoformat()
    task_id = new_id("task")
    task = {
        "id": task_id,
        "type": payload.get("type") or "mock.long_running",
        "title": payload.get("title") or "模拟长任务",
        "status": payload.get("status") or "pending",
        "actor_id": actor_id,
        "resource": payload.get("resource") or "local-server",
        "risk_level": payload.get("risk_level") or "read",
        "reason": payload.get("reason") or "验证任务事件底座",
        "created_at": current,
        "started_at": None,
        "finished_at": None,
        "cancel_requested": 0,
        "failure_reason": None,
        "approval_id": payload.get("approval_id"),
    }
    with connect() as con:
        con.execute(
            "insert into ops_tasks values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task["id"],
                task["type"],
                task["title"],
                task["status"],
                task["actor_id"],
                task["resource"],
                task["risk_level"],
                task["reason"],
                task["created_at"],
                task["started_at"],
                task["finished_at"],
                task["cancel_requested"],
                task["failure_reason"],
                task["approval_id"],
            ),
        )
    add_ops_event("task", task_id, "task_created", {"title": task["title"], "risk_level": task["risk_level"]})
    created = get_ops_task(task_id)
    if created is None:
        raise RuntimeError(f"failed to create task {task_id}")
    return created


def list_ops_tasks(limit: int = 100) -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            "select * from ops_tasks order by created_at desc limit ?", (limit,)
        ).fetchall()
    return [_task_from_row(row) for row in rows]


def get_ops_task(task_id: str) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("select * from ops_tasks where id = ?", (task_id,)).fetchone()
    return _task_from_row(row) if row else None


def get_ops_task_by_approval_id(approval_id: str) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute(
            "select * from ops_tasks where approval_id = ?", (approval_id,)
        ).fetchone()
    return _task_from_row(row) if row else None


def update_ops_task_status(
    task_id: str,
    status: str,
    *,
    started_at: str | None = None,
    finished_at: str | None = None,
    failure_reason: str | None = None,
) -> None:
    with connect() as con:
        con.execute(
            """
            update ops_tasks
            set status = ?,
                started_at = coalesce(?, started_at),
                finished_at = coalesce(?, finished_at),
                failure_reason = coalesce(?, failure_reason)
            where id = ?
            """,
            (status, started_at, finished_at, failure_reason, task_id),
        )


def request_cancel_ops_task(task_id: str) -> dict[str, Any] | None:
    with connect() as con:
        existing = con.execute("select * from ops_tasks where id = ?", (task_id,)).fetchone()
        if not existing:
            return None
        final_status = "cancelled" if existing["status"] in ("pending", "running", "waiting_approval") else existing["status"]
        con.execute(
            "update ops_tasks set cancel_requested = 1, status = ?, finished_at = coalesce(finished_at, ?) where id = ?",
            (final_status, now().isoformat(), task_id),
        )
    add_ops_event("task", task_id, "cancel_requested", {"status": final_status})
    return get_ops_task(task_id)


def add_ops_subtask(
    task_id: str,
    title: str,
    status: str,
    sequence: int,
    *,
    rollback_step: bool = False,
    output_ref: str | None = None,
    failure_reason: str | None = None,
) -> dict[str, Any]:
    subtask_id = new_id("subtask")
    current = now().isoformat()
    with connect() as con:
        con.execute(
            "insert into ops_subtasks values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                subtask_id,
                task_id,
                title,
                status,
                sequence,
                current,
                current if status in ("succeeded", "failed", "cancelled") else None,
                1 if rollback_step else 0,
                output_ref,
                failure_reason,
            ),
        )
    return {
        "id": subtask_id,
        "task_id": task_id,
        "title": title,
        "status": status,
        "sequence": sequence,
        "started_at": current,
        "finished_at": current if status in ("succeeded", "failed", "cancelled") else None,
        "rollback_step": rollback_step,
        "output_ref": output_ref,
        "failure_reason": failure_reason,
    }


def list_ops_subtasks(task_id: str) -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            "select * from ops_subtasks where task_id = ? order by sequence asc", (task_id,)
        ).fetchall()
    return [
        {
            **dict(row),
            "rollback_step": bool(row["rollback_step"]),
        }
        for row in rows
    ]


def _next_ops_event_sequence(scope: str, scope_id: str) -> int:
    with connect() as con:
        row = con.execute(
            "select coalesce(max(sequence), 0) as sequence from ops_events where scope = ? and scope_id = ?",
            (scope, scope_id),
        ).fetchone()
    return int(row["sequence"]) + 1


def add_ops_event(
    scope: str,
    scope_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    sequence: int | None = None,
) -> dict[str, Any]:
    event_id = new_id("evt")
    event_sequence = sequence or _next_ops_event_sequence(scope, scope_id)
    current = now().isoformat()
    with connect() as con:
        con.execute(
            "insert into ops_events values (?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                scope,
                scope_id,
                event_sequence,
                event_type,
                json.dumps(payload),
                current,
            ),
        )
    return {
        "id": event_id,
        "scope": scope,
        "scope_id": scope_id,
        "sequence": event_sequence,
        "type": event_type,
        "payload": payload,
        "created_at": current,
    }


def list_ops_events(scope: str | None = None, scope_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if scope:
        clauses.append("scope = ?")
        params.append(scope)
    if scope_id:
        clauses.append("scope_id = ?")
        params.append(scope_id)
    where = f"where {' and '.join(clauses)}" if clauses else ""
    with connect() as con:
        rows = con.execute(
            f"select * from ops_events {where} order by created_at asc, sequence asc limit ?",
            (*params, limit),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "scope": row["scope"],
            "scope_id": row["scope_id"],
            "sequence": row["sequence"],
            "type": row["type"],
            "payload": json.loads(row["payload"]),
            "created_at": row["created_at"],
        }
        for row in rows
    ]
