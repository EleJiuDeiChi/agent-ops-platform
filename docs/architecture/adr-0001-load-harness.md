# ADR-0001: Use pinned httpx as the first load-harness engine

- Status: Accepted for R0 baseline
- Date: 2026-07-11
- Owners: architecture, backend QA, performance QA

## Context

The production test specification requires k6 or an ADR-selected equivalent
load tool to be introduced and version-locked in R0. The backend already pins
`httpx==0.28.1`, while the repository rule forbids adding a dependency without
explicit approval. The immediate R0 need is a reproducible HTTP concurrency
smoke harness; R2 later needs SSE ordering, resume and latency collection.

## Decision

Use `httpx.AsyncClient==0.28.1` through `scripts/run-load-smoke.py` as the R0
equivalent load engine. The harness:

- bounds concurrency, request count and timeouts;
- defaults to loopback targets and requires an explicit flag for other hosts;
- records the Test Spec execution-contract fields, status counts, errors and
  p50/p95/p99 latency in JSON;
- executes from the pinned backend image during production-topology tests, so
  clean VMs and release runners do not depend on an unversioned host package;
- has an offline self-test that runs in CI and the production baseline check;
- never claims GA performance from a local or mocked result.

The tool version is locked by `backend/requirements.txt`; changing it requires
an ADR update and harness self-test review.

## Rejected alternatives

- Add k6 now: rejected because it would add a new external dependency without
  explicit approval and would duplicate the already-pinned HTTP client for the
  limited R0 smoke profile.
- Treat Playwright timing as load evidence: rejected because one browser path
  cannot generate or measure the required concurrent API/SSE profile.
- Use an unversioned system `curl` loop: rejected because it has no structured
  concurrency, percentile or manifest contract.

## Consequences and future gate

This decision satisfies tool selection and version locking, not GA-PERF-001.
Before R2 closes, extend the harness or replace it through a superseding ADR to
support 100 concurrent resumable SSE streams, at least 1,000 events, NTP offset
evidence and three frozen-profile runs. Staging/canary results must use the same
release digest and cannot be replaced by the offline self-test.
