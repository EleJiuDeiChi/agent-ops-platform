#!/usr/bin/env python3
"""Bounded HTTP concurrency harness for the R0 load-tool contract."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlparse

import httpx


LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
RELEASE_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class LoadHarnessError(RuntimeError):
    pass


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile / 100 * len(ordered)) - 1)
    return round(ordered[index], 3)


def _validate_target(base_url: str, *, allow_non_loopback: bool) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise LoadHarnessError("base URL must be an HTTP(S) origin")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise LoadHarnessError("base URL must not contain credentials, query or fragment")
    if not allow_non_loopback and parsed.hostname not in LOOPBACK_HOSTS:
        raise LoadHarnessError("non-loopback load targets require --allow-non-loopback")
    return base_url.rstrip("/") + "/"


def _validate_path(path: str) -> str:
    parsed = urlparse(path)
    if (
        not path.startswith("/")
        or parsed.scheme
        or parsed.netloc
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise LoadHarnessError(
            "path must be an absolute origin-relative path without query or fragment"
        )
    return path


async def _run_request(
    client: httpx.AsyncClient,
    url: str,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    async with semaphore:
        started = perf_counter()
        try:
            response = await client.get(url)
            return {
                "status": response.status_code,
                "latency_ms": (perf_counter() - started) * 1000,
                "error": None,
            }
        except httpx.HTTPError as exc:
            return {
                "status": None,
                "latency_ms": (perf_counter() - started) * 1000,
                "error": type(exc).__name__,
            }


async def run_load(
    *,
    base_url: str,
    path: str,
    requests: int,
    concurrency: int,
    timeout_seconds: float,
    allow_non_loopback: bool,
    insecure: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    if not 1 <= requests <= 10_000:
        raise LoadHarnessError("requests must be between 1 and 10000")
    if not 1 <= concurrency <= min(requests, 1_000):
        raise LoadHarnessError("concurrency must be between 1 and requests")
    if not 0 < timeout_seconds <= 300:
        raise LoadHarnessError("timeout must be greater than 0 and at most 300 seconds")
    origin = _validate_target(base_url, allow_non_loopback=allow_non_loopback)
    if insecure and urlparse(origin).hostname not in LOOPBACK_HOSTS:
        raise LoadHarnessError("--insecure is allowed only for loopback verification")
    target = origin.rstrip("/") + _validate_path(path)
    timeout = httpx.Timeout(timeout_seconds)
    limits = httpx.Limits(
        max_connections=concurrency,
        max_keepalive_connections=concurrency,
    )
    started = perf_counter()
    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        verify=not insecure,
        transport=transport,
    ) as client:
        semaphore = asyncio.Semaphore(concurrency)
        rows = await asyncio.gather(
            *(_run_request(client, target, semaphore) for _ in range(requests))
        )
    duration_seconds = perf_counter() - started
    latencies = [float(row["latency_ms"]) for row in rows]
    status_counts = Counter(str(row["status"]) for row in rows if row["status"] is not None)
    error_counts = Counter(str(row["error"]) for row in rows if row["error"])
    successful = sum(
        1 for row in rows if isinstance(row["status"], int) and 200 <= row["status"] < 400
    )
    return {
        "target": target,
        "requests": requests,
        "concurrency": concurrency,
        "successful_requests": successful,
        "failed_requests": requests - successful,
        "duration_seconds": round(duration_seconds, 3),
        "requests_per_second": round(requests / duration_seconds, 3),
        "latency_ms": {
            "p50": _percentile(latencies, 50),
            "p95": _percentile(latencies, 95),
            "p99": _percentile(latencies, 99),
            "max": round(max(latencies, default=0.0), 3),
        },
        "status_counts": dict(sorted(status_counts.items())),
        "error_counts": dict(sorted(error_counts.items())),
    }


async def _self_test() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "alive"})

    result = await run_load(
        base_url="http://127.0.0.1:8080",
        path="/health/live",
        requests=20,
        concurrency=4,
        timeout_seconds=1,
        allow_non_loopback=False,
        transport=httpx.MockTransport(handler),
    )
    if result["successful_requests"] != 20 or result["failed_requests"] != 0:
        raise LoadHarnessError("offline self-test request accounting failed")
    if result["status_counts"] != {"200": 20}:
        raise LoadHarnessError("offline self-test status accounting failed")
    if set(result["latency_ms"]) != {"p50", "p95", "p99", "max"}:
        raise LoadHarnessError("offline self-test percentile output failed")
    try:
        await run_load(
            base_url="https://127.0.0.1:8443",
            path="https://example.test/escaped",
            requests=1,
            concurrency=1,
            timeout_seconds=1,
            allow_non_loopback=False,
            insecure=True,
            transport=httpx.MockTransport(handler),
        )
    except LoadHarnessError:
        pass
    else:
        raise LoadHarnessError("absolute URL path bypass self-test failed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--base-url")
    parser.add_argument("--path", default="/health/live")
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout-seconds", type=float, default=5)
    parser.add_argument("--allow-non-loopback", action="store_true")
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--evidence-path")
    parser.add_argument("--release-digest", default=os.getenv("AIOPS_RELEASE_DIGEST"))
    args = parser.parse_args()
    if args.self_test:
        try:
            asyncio.run(_self_test())
        except LoadHarnessError as exc:
            print(f"load harness self-test failed: {exc}", file=sys.stderr)
            return 1
        print("load harness self-test passed")
        return 0
    if not args.base_url or args.output is None or not args.release_digest:
        parser.error("--base-url, --output and --release-digest are required")
    if not RELEASE_DIGEST_PATTERN.fullmatch(args.release_digest):
        parser.error("--release-digest must use sha256:<64 lowercase hex>")
    try:
        result = asyncio.run(
            run_load(
                base_url=args.base_url,
                path=args.path,
                requests=args.requests,
                concurrency=args.concurrency,
                timeout_seconds=args.timeout_seconds,
                allow_non_loopback=args.allow_non_loopback,
                insecure=args.insecure,
            )
        )
    except LoadHarnessError as exc:
        print(f"load harness failed: {exc}", file=sys.stderr)
        return 1
    passed = result["failed_requests"] == 0
    manifest = {
        "test_id": "GA-R0-LOAD-TOOL",
        "requirement": "version-locked bounded HTTP load harness",
        "automation": "scripts/run-load-smoke.py with httpx.AsyncClient==0.28.1",
        "environment": "operator-specified target",
        "fixture_or_seed": result["target"],
        "sample_size_or_duration": {
            "requests": result["requests"],
            "concurrency": result["concurrency"],
            "duration_seconds": result["duration_seconds"],
        },
        "expected": "all bounded smoke requests return HTTP 2xx/3xx",
        "evidence_path": args.evidence_path or args.output,
        "owner": "performance QA",
        "release_digest": args.release_digest,
        "result": "passed" if passed else "failed",
        "measurements": result,
        "executed_at": datetime.now(UTC).isoformat(),
    }
    rendered = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if args.output == "-":
        sys.stdout.write(rendered)
        return 0 if passed else 1
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output_path.with_name(
        f".{output_path.name}.tmp-{os.getpid()}"
    )
    temporary_output.write_text(
        rendered,
        encoding="utf-8",
    )
    temporary_output.chmod(0o600)
    temporary_output.replace(output_path)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
