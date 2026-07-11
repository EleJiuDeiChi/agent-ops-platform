#!/usr/bin/env python3
"""Assemble the canonical GA-R0-001 manifest from cross-bound evidence."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from app.ai.verification import (  # noqa: E402
    REQUIRED_CHECKS,
    provider_manifest_validation_error,
)
from app.ai.provider_profiles import LIVE_PROVIDER_IDS, get_provider_profile  # noqa: E402


CANONICAL_DIR = Path(".omx/evidence/production-ga/GA-R0-001")
OCI_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class EvidenceError(RuntimeError):
    """Raised when evidence cannot support a passed R0 baseline."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceError(message)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"evidence is not readable JSON: {path}") from exc
    require(isinstance(payload, dict), f"evidence must be an object: {path}")
    return payload


def file_digest(path: Path) -> str:
    try:
        return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
    except OSError as exc:
        raise EvidenceError(f"evidence is not readable: {path}") from exc


def protected_trust_anchor(path: Path) -> Path:
    try:
        require(not path.is_symlink(), "review allowed-signers trust anchor is a symlink")
        metadata = path.stat()
    except OSError as exc:
        raise EvidenceError("review allowed-signers trust anchor is unreadable") from exc
    require(stat.S_ISREG(metadata.st_mode), "review allowed-signers trust anchor is not regular")
    require(metadata.st_uid in {0, os.geteuid()}, "review allowed-signers trust anchor has an untrusted owner")
    require(
        stat.S_IMODE(metadata.st_mode) & 0o022 == 0,
        "review allowed-signers trust anchor is writable by group or other users",
    )
    require(0 < metadata.st_size <= 64 * 1024, "review allowed-signers trust anchor has an invalid size")
    return path.resolve()


def reviewer_key_fingerprint(allowed_signers_path: Path, identity: str) -> str:
    matches: set[str] = set()
    for raw_line in allowed_signers_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if identity not in fields[0].split(","):
            continue
        key_index = next(
            (
                index
                for index, value in enumerate(fields[1:], start=1)
                if value.startswith(("ssh-", "ecdsa-", "sk-"))
            ),
            None,
        )
        require(
            key_index is not None and key_index + 1 < len(fields),
            f"reviewer {identity} has an invalid allowed-signers entry",
        )
        try:
            key_blob = base64.b64decode(fields[key_index + 1], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise EvidenceError(f"reviewer {identity} has an invalid SSH public key") from exc
        matches.add("sha256:" + hashlib.sha256(key_blob).hexdigest())
    require(len(matches) == 1, f"reviewer {identity} must resolve to exactly one trusted SSH key")
    return next(iter(matches))


def canonical_evidence_path(root: Path, path: Path, directory: Path) -> Path:
    lexical_root = root.absolute()
    candidate = path.absolute() if path.is_absolute() else lexical_root / path
    try:
        relative = candidate.relative_to(lexical_root)
    except ValueError as exc:
        raise EvidenceError(f"evidence escapes repository: {path}") from exc
    require(".." not in relative.parts, f"evidence path contains traversal: {path}")
    require(relative.is_relative_to(directory), f"evidence is outside {directory}: {path}")
    current = lexical_root
    for part in relative.parts:
        current = current / part
        require(not current.is_symlink(), f"evidence path contains a symlink: {path}")
    require(candidate.is_file(), f"evidence file is missing: {path}")
    root = lexical_root.resolve()
    resolved_directory = (root / directory).resolve()
    candidate = candidate.resolve()
    require(candidate.is_relative_to(root), f"evidence escapes repository: {path}")
    require(
        candidate.is_relative_to(resolved_directory),
        f"evidence resolves outside {directory}: {path}",
    )
    return candidate


def parse_time(value: Any, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceError(f"{field} must be ISO-8601") from exc
    require(parsed.tzinfo is not None, f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def validate_release(
    payload: dict[str, Any],
    *,
    commit: str,
    expected_repository: str,
) -> dict[str, str]:
    validator_path = REPOSITORY_ROOT / "scripts/validate-ga-manifest.py"
    spec = importlib.util.spec_from_file_location("r0_ga_manifest_validator", validator_path)
    require(spec is not None and spec.loader is not None, "release validator could not be loaded")
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    try:
        validator.validate_manifest(payload, expected_repository=expected_repository)
    except validator.ManifestError as exc:
        raise EvidenceError(f"release manifest is invalid: {exc}") from exc
    require(payload.get("test_id") == "GA-R0-002", "release test_id mismatch")
    require(payload.get("result") == "passed", "release manifest is not passed")
    require(payload.get("commit_sha") == commit, "release commit mismatch")
    require(
        payload.get("evidence_path") == ".omx/evidence/production-ga/GA-R0-002/",
        "release evidence path is not canonical",
    )
    digests = payload.get("release_digests")
    require(isinstance(digests, dict), "release digest map is missing")
    for name in ("backend", "frontend"):
        require(
            OCI_DIGEST_RE.fullmatch(str(digests.get(name) or "")) is not None,
            f"release {name} digest is invalid",
        )
        image = payload.get(name)
        require(isinstance(image, dict), f"release {name} image metadata is missing")
        require(image.get("digest") == digests[name], f"release {name} digest mismatch")
        require(isinstance(image.get("image"), str) and image["image"], f"release {name} image is missing")
    require(payload.get("release_digest") == digests["backend"], "release primary digest mismatch")
    tag_signature = payload.get("tag_signature")
    require(isinstance(tag_signature, dict), "release tag signature evidence is missing")
    require(tag_signature.get("verified") is True, "release tag signature is not verified")
    require(tag_signature.get("format") == "ssh", "release tag signature format is not SSH")
    for field in ("allowed_signers_sha256", "verification_log_sha256"):
        require(
            OCI_DIGEST_RE.fullmatch(str(tag_signature.get(field) or "")) is not None,
            f"release tag signature {field} is invalid",
        )
    return {name: str(digests[name]) for name in ("backend", "frontend")}


def validate_matrix(
    summary: dict[str, Any],
    status: dict[str, Any],
    *,
    commit: str,
    summary_path: Path,
    root: Path,
    release_digests: dict[str, str],
    release_manifest_path: Path,
    release_tag: str,
    release_image_refs: dict[str, str],
) -> None:
    require(summary.get("test_id") == "GA-R0-001", "matrix test_id mismatch")
    require(summary.get("result") == "passed", "matrix summary is not passed")
    require(summary.get("commit_sha") == commit, "matrix commit mismatch")
    require(summary.get("artifact_class") == "signed_release", "matrix did not test signed release artifacts")
    require(summary.get("release_digest") == release_digests["backend"], "matrix backend release digest mismatch")
    require(
        summary.get("frontend_release_digest") == release_digests["frontend"],
        "matrix frontend release digest mismatch",
    )
    require(summary.get("release_tag") == release_tag, "matrix release tag mismatch")
    require(
        summary.get("upstream_backend_image_ref") == release_image_refs["backend"],
        "matrix backend image reference mismatch",
    )
    require(
        summary.get("upstream_frontend_image_ref") == release_image_refs["frontend"],
        "matrix frontend image reference mismatch",
    )
    expected_release_manifest_sha256 = file_digest(release_manifest_path)
    require(
        summary.get("release_manifest_sha256") == expected_release_manifest_sha256,
        "matrix release manifest hash mismatch",
    )
    release_copy_path = canonical_evidence_path(
        root,
        Path(str(summary.get("release_manifest_path") or "")),
        CANONICAL_DIR,
    )
    require(
        file_digest(release_copy_path) == expected_release_manifest_sha256,
        "matrix release manifest copy does not match GA-R0-002",
    )
    require(status.get("result") == "passed", "matrix status is not passed")
    require(status.get("commit_sha") == commit, "matrix status commit mismatch")
    require(status.get("run_id") == summary.get("run_id"), "matrix run ID mismatch")
    require(
        status.get("summary_sha256") == file_digest(summary_path).removeprefix("sha256:"),
        "matrix summary hash mismatch",
    )
    generator_path = REPOSITORY_ROOT / "scripts/generate-r0-matrix-summary.py"
    spec = importlib.util.spec_from_file_location("r0_matrix_summary_validator", generator_path)
    require(spec is not None and spec.loader is not None, "matrix validator could not be loaded")
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    try:
        rebuilt = generator.build_summary(
            root=root,
            evidence_dir=root / CANONICAL_DIR,
            commit=commit,
            source_sha=str(summary.get("source_archive_sha256") or ""),
            registry_image=str(summary.get("registry_image") or ""),
            run_id=str(summary.get("run_id") or ""),
        )
    except generator.ValidationError as exc:
        raise EvidenceError(f"matrix evidence is invalid: {exc}") from exc
    supplied = dict(summary)
    supplied.pop("executed_at", None)
    rebuilt.pop("executed_at", None)
    require(rebuilt == supplied, "matrix summary does not match regenerated evidence")


def validate_llm(
    payload: dict[str, Any],
    *,
    release_digest: str,
    commit: str,
    now: datetime,
) -> None:
    provider_id = str(payload.get("provider_id") or "")
    require(provider_id in LIVE_PROVIDER_IDS, "LLM provider is not supported")
    require(payload.get("test_id") == f"GA-R0-001-LLM-{provider_id}", "LLM test_id mismatch")
    try:
        get_provider_profile(provider_id).validate_base_url(
            str(payload.get("provider_origin") or ""),
            production=True,
        )
    except ValueError as exc:
        raise EvidenceError(f"LLM provider origin is invalid: {exc}") from exc
    require(payload.get("result") == "pass", "LLM contract is not passed")
    validation_error = provider_manifest_validation_error(
        payload,
        current_time=now,
        expected_provider_id=provider_id,
        expected_provider_origin=str(payload.get("provider_origin") or ""),
        expected_model=str(payload.get("model") or ""),
        expected_release_digest=release_digest,
        expected_commit_sha=commit,
    )
    require(validation_error is None, f"LLM provider manifest is invalid: {validation_error}")
    checks = payload.get("required_checks")
    require(
        isinstance(checks, dict) and set(checks) == set(REQUIRED_CHECKS),
        "LLM required checks are missing or unexpected",
    )
    require(all(value is True for value in checks.values()), "LLM required check is not true")
    policy = payload.get("policy")
    require(isinstance(policy, dict), "LLM policy evidence is missing")
    approvals = policy.get("owner_approvals")
    require(isinstance(approvals, dict), "LLM owner approvals are missing")
    require(set(approvals) == {"ai", "security", "privacy"}, "LLM owner roles are incomplete")


def verify_review_signature(
    statement_path: Path,
    signature_path: Path,
    allowed_signers_path: Path,
    reviewer_identity: str,
) -> bool:
    try:
        completed = subprocess.run(
            [
                "ssh-keygen", "-Y", "verify", "-f", str(allowed_signers_path),
                "-I", reviewer_identity, "-n", "aiops-r0-review", "-s", str(signature_path),
            ],
            input=statement_path.read_bytes(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def validate_reviews(
    payload: dict[str, Any],
    *,
    root: Path,
    commit: str,
    now: datetime,
    evidence_bindings: dict[str, str],
    allowed_signers_path: Path,
    signature_verifier: Callable[[Path, Path, Path, str], bool],
) -> tuple[dict[str, Path], dict[str, str]]:
    require(payload.get("test_id") == "GA-R0-001-REVIEW", "review test_id mismatch")
    require(payload.get("result") == "passed", "R0 review is not passed")
    require(payload.get("commit_sha") == commit, "R0 review commit mismatch")
    reviews = payload.get("reviews")
    require(isinstance(reviews, dict), "R0 reviews are missing")
    require(set(reviews) == {"architecture", "security"}, "R0 review roles are incomplete")
    require(
        "allowed_signers_path" not in payload,
        "review manifest must not nominate its own trust anchor",
    )
    allowed_signers_path = protected_trust_anchor(allowed_signers_path)
    identities: list[str] = []
    key_fingerprints: dict[str, str] = {}
    artifacts: dict[str, Path] = {}
    for role, expected in (("architecture", "CLEAR"), ("security", "APPROVE")):
        review = reviews[role]
        require(isinstance(review, dict), f"{role} review is invalid")
        require(review.get("verdict") == expected, f"{role} review verdict is not {expected}")
        identity = str(review.get("reviewer_identity") or "").strip()
        require(identity, f"{role} reviewer identity is missing")
        identities.append(identity.casefold())
        key_fingerprints[role] = reviewer_key_fingerprint(allowed_signers_path, identity)
        reviewed_at = parse_time(review.get("reviewed_at"), field=f"{role}.reviewed_at")
        require(reviewed_at <= now, f"{role} review is dated in the future")
        statement_path = canonical_evidence_path(
            root,
            Path(str(review.get("statement_path") or "")),
            CANONICAL_DIR,
        )
        signature_path = canonical_evidence_path(
            root,
            Path(str(review.get("signature_path") or "")),
            CANONICAL_DIR,
        )
        statement = read_json(statement_path)
        require(statement.get("role") == role, f"{role} signed statement role mismatch")
        require(statement.get("verdict") == expected, f"{role} signed statement verdict mismatch")
        require(statement.get("reviewer_identity") == identity, f"{role} signed identity mismatch")
        require(statement.get("reviewed_at") == review.get("reviewed_at"), f"{role} signed time mismatch")
        require(statement.get("commit_sha") == commit, f"{role} signed commit mismatch")
        require(
            statement.get("evidence_bindings") == evidence_bindings,
            f"{role} signed evidence bindings mismatch",
        )
        require(
            signature_verifier(
                statement_path,
                signature_path,
                allowed_signers_path,
                identity,
            ),
            f"{role} review SSH signature is invalid",
        )
        artifacts[f"{role}_review_statement"] = statement_path
        artifacts[f"{role}_review_signature"] = signature_path
    require(len(set(identities)) == 2, "architecture and security reviewers must be distinct")
    require(
        len(set(key_fingerprints.values())) == 2,
        "architecture and security reviewers must use distinct trusted SSH keys",
    )
    return artifacts, key_fingerprints


def assemble(
    *,
    root: Path,
    commit: str,
    matrix_summary_path: Path,
    matrix_status_path: Path,
    release_manifest_path: Path,
    llm_manifest_path: Path,
    review_manifest_path: Path,
    expected_repository: str,
    review_allowed_signers_path: Path,
    now: datetime,
    signature_verifier: Callable[[Path, Path, Path, str], bool] = verify_review_signature,
) -> dict[str, Any]:
    require(SHA_RE.fullmatch(commit) is not None, "commit must be a full Git SHA")
    matrix_summary_path = canonical_evidence_path(root, matrix_summary_path, CANONICAL_DIR)
    matrix_status_path = canonical_evidence_path(root, matrix_status_path, CANONICAL_DIR)
    llm_manifest_path = canonical_evidence_path(root, llm_manifest_path, CANONICAL_DIR)
    review_manifest_path = canonical_evidence_path(root, review_manifest_path, CANONICAL_DIR)
    release_manifest_path = canonical_evidence_path(
        root,
        release_manifest_path,
        Path(".omx/evidence/production-ga/GA-R0-002"),
    )
    root = root.resolve()
    release = read_json(release_manifest_path)
    release_digests = validate_release(
        release,
        commit=commit,
        expected_repository=expected_repository,
    )
    matrix = read_json(matrix_summary_path)
    matrix_status = read_json(matrix_status_path)
    validate_matrix(
        matrix,
        matrix_status,
        commit=commit,
        summary_path=matrix_summary_path,
        root=root,
        release_digests=release_digests,
        release_manifest_path=release_manifest_path,
        release_tag=str(release["release_tag"]),
        release_image_refs={
            name: f"{release[name]['image']}@{release_digests[name]}"
            for name in ("backend", "frontend")
        },
    )
    llm = read_json(llm_manifest_path)
    validate_llm(
        llm,
        release_digest=release_digests["backend"],
        commit=commit,
        now=now,
    )
    reviews = read_json(review_manifest_path)
    evidence_bindings = {
        "matrix_summary_sha256": file_digest(matrix_summary_path),
        "release_manifest_sha256": file_digest(release_manifest_path),
        "llm_manifest_sha256": file_digest(llm_manifest_path),
    }
    review_artifacts, reviewer_key_fingerprints = validate_reviews(
        reviews,
        root=root,
        commit=commit,
        now=now,
        evidence_bindings=evidence_bindings,
        allowed_signers_path=review_allowed_signers_path,
        signature_verifier=signature_verifier,
    )
    paths = {
        "matrix_summary": matrix_summary_path,
        "matrix_status": matrix_status_path,
        "release_manifest": release_manifest_path,
        "llm_manifest": llm_manifest_path,
        "review_manifest": review_manifest_path,
    }
    paths.update(review_artifacts)
    return {
        "test_id": "GA-R0-001",
        "requirement": "production fail-closed, setup, health, support matrix, signed release and live LLM contract",
        "automation": "assemble-r0-baseline-manifest.py over cross-bound blocking evidence",
        "environment": "current signed release on clean Ubuntu 22.04/24.04 x86_64 ext4/xfs plus live provider",
        "fixture_or_seed": "four clean KVM VMs, exact signed release digests, one live provider and distinct architecture/security reviews",
        "sample_size_or_duration": "four clean VM lifecycles and one unexpired provider contract probe",
        "expected": "all R0 gates pass for the same commit and backend/frontend release digests",
        "evidence_path": f"{CANONICAL_DIR.as_posix()}/",
        "owner": "backend and release leads",
        "release_digest": release_digests["backend"],
        "release_digests": release_digests,
        "result": "passed",
        "commit_sha": commit,
        "release_tag": release["release_tag"],
        "assembled_at": now.isoformat(),
        "trust_anchors": {
            "review_allowed_signers_sha256": file_digest(
                protected_trust_anchor(review_allowed_signers_path)
            ),
            "reviewer_key_fingerprints": reviewer_key_fingerprints,
        },
        "evidence": {
            name: {
                "path": path.relative_to(root).as_posix(),
                "sha256": file_digest(path),
            }
            for name, path in paths.items()
        },
    }


def write_atomically(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def valid_llm_fixture(commit: str, release_digest: str, now: datetime) -> dict[str, Any]:
    policy_digest = "sha256:" + "d" * 64
    quota_digest = "sha256:" + "e" * 64
    quota = {
        "concurrency_limit": 10,
        "requests_per_minute": "provider-managed",
        "tokens_per_minute": "provider-managed",
        "tokens_per_day": "unlimited",
        "balance_alert_threshold": "1",
    }
    prices = {"cache_hit_input": "0.1", "cache_miss_input": "1", "output": "2"}
    return {
        "schema_version": 2,
        "test_id": "GA-R0-001-LLM-deepseek",
        "result": "pass",
        "executed_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=24)).isoformat(),
        "runner_identity": "self-test",
        "release_digest": release_digest,
        "commit_sha": commit,
        "evidence_path": ".omx/evidence/production-ga/GA-R0-001/llm.json",
        "provider_id": "deepseek",
        "feature_flag": {"enabled_provider_id": "deepseek", "exclusive": True},
        "provider_origin": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "provider_capabilities": {"streaming": True, "tool_calls": True, "usage_reporting": True},
        "account_identifier": "self-test-account",
        "region": "PRC",
        "model_discovery": {
            "method": "authenticated_models_endpoint",
            "path": "/models",
            "model_confirmed": True,
            "response_sha256": "sha256:" + "1" * 64,
        },
        "tool_call": {
            "name": "probe_echo",
            "arguments": {"value": "r0-live-contract-probe"},
            "schema_match": True,
        },
        "invalid_tool_rejection": {"unknown_tool_rejected": True, "extra_field_rejected": True},
        "stream": {
            "json_chunks": 1,
            "done_received": True,
            "contract_marker_received": True,
            "usage_reported": True,
            "content_type": "text/event-stream",
            "normalized_events_sha256": "sha256:" + "2" * 64,
        },
        "timeout_retry": {
            "connect_timeout_seconds": 1,
            "read_timeout_seconds": 2,
            "max_retries": 1,
            "attempts": {"model_discovery": 1, "tool_call": 1, "streaming": 1},
        },
        "redaction_capture": {
            "seeded_secret_absent": True,
            "replacement_marker_present": True,
            "capture_sha256": "sha256:" + "3" * 64,
        },
        "usage_cost": {
            "input_tokens": 10,
            "cached_input_tokens": 2,
            "cache_miss_input_tokens": 8,
            "output_tokens": 5,
            "estimated_cost": "0.001",
            "successful_responses_estimated_cost": "0.001",
            "retry_cost_upper_bound": "0",
            "preflight_worst_case_cost": "0.002",
            "max_input_tokens_per_probe": 512,
            "input_payload_byte_upper_bound": 400,
            "tool_attempt_budget": 2,
            "currency": "CNY",
            "approved_cap": "0.01",
            "rates_per_million_tokens": prices,
        },
        "quota": quota,
        "quota_pricing_evidence": {
            "account_tier": "self-test-tier",
            "source_url": "https://example.invalid/pricing",
            "source_content_sha256": "sha256:" + "4" * 64,
            "snapshot_sha256": quota_digest,
            "snapshot_schema_version": 1,
            "reviewed_at": now.isoformat(),
            "currency": "CNY",
            "prices_per_million_tokens": prices,
            "quota": quota,
        },
        "policy": {
            "region": "PRC",
            "retention": "reviewed",
            "training_opt_out_confirmed": True,
            "redacted_operational_data_only": True,
            "ai_owner": "ai-owner",
            "security_owner": "security-owner",
            "privacy_owner": "privacy-owner",
            "provider_policy_url": "https://example.invalid/privacy",
            "provider_policy_sha256": policy_digest,
            "reviewed_at": now.isoformat(),
            "owner_approvals": {
                role: {
                    "role": role,
                    "approver_identity": f"{role}-owner",
                    "approved": True,
                    "approved_at": now.isoformat(),
                    "provider_policy_sha256": policy_digest,
                    "quota_pricing_snapshot_sha256": quota_digest,
                }
                for role in ("ai", "security", "privacy")
            },
        },
        "required_checks": {name: True for name in REQUIRED_CHECKS},
    }


def self_test() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        commit = "a" * 40
        backend = "sha256:" + "b" * 64
        frontend = "sha256:" + "c" * 64
        now = datetime.now(timezone.utc)
        r0_directory = root / CANONICAL_DIR
        release_directory = root / ".omx/evidence/production-ga/GA-R0-002"
        r0_directory.mkdir(parents=True)
        release_directory.mkdir(parents=True)
        summary_path = r0_directory / "matrix-summary.json"
        status_path = r0_directory / "matrix-status.json"
        release_path = release_directory / "manifest.json"
        llm_path = r0_directory / "llm.json"
        review_path = r0_directory / "review.json"
        release_path.write_text(json.dumps({
            "test_id": "GA-R0-002", "result": "passed", "commit_sha": commit,
            "evidence_path": ".omx/evidence/production-ga/GA-R0-002/",
            "release_digest": backend, "release_digests": {"backend": backend, "frontend": frontend},
            "release_tag": "v1.2.3",
            "repository": "example/repo",
            "workflow_ref": "example/repo/.github/workflows/r0-ci.yml@refs/tags/v1.2.3",
            "certificate_identity": "https://github.com/example/repo/.github/workflows/r0-ci.yml@refs/tags/v1.2.3",
            "oidc_issuer": "https://token.actions.githubusercontent.com",
            "backend": {"image": "ghcr.io/example/agent-ops-backend", "digest": backend},
            "frontend": {"image": "ghcr.io/example/agent-ops-frontend", "digest": frontend},
            "tag_signature": {"verified": True, "format": "ssh", "allowed_signers_sha256": backend, "verification_log_sha256": frontend},
            "requirement": "fixture", "automation": "fixture", "environment": "fixture",
            "fixture_or_seed": "fixture", "sample_size_or_duration": "fixture",
            "expected": "fixture", "owner": "fixture",
        }), encoding="utf-8")
        generator_path = REPOSITORY_ROOT / "scripts/generate-r0-matrix-summary.py"
        spec = importlib.util.spec_from_file_location("r0_matrix_self_test", generator_path)
        require(spec is not None and spec.loader is not None, "matrix self-test validator could not be loaded")
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        runs, context = generator.synthetic_runs()
        context["commit"] = commit
        context["run_id"] = "fixture"
        release_copy = r0_directory / "release-manifest-fixture.json"
        release_copy.write_bytes(release_path.read_bytes())
        release_sha = file_digest(release_path)
        shared_fixture = b"{}\n"
        shared_sha = f"sha256:{hashlib.sha256(shared_fixture).hexdigest()}"
        for name in (
            "backend-cosign", "frontend-cosign", "backend-provenance",
            "frontend-provenance", "backend-sbom", "frontend-sbom",
        ):
            (r0_directory / f"{name}-fixture.json").write_bytes(shared_fixture)
        for name, run in runs.items():
            run.update({
                "commit_sha": commit,
                "release_digest": backend,
                "frontend_release_digest": frontend,
                "upstream_backend_image_ref": f"ghcr.io/example/agent-ops-backend@{backend}",
                "upstream_frontend_image_ref": f"ghcr.io/example/agent-ops-frontend@{frontend}",
                "release_manifest_sha256": release_sha,
                "release_manifest_path": f"{CANONICAL_DIR.as_posix()}/release-manifest-fixture.json",
                "release_tag": "v1.2.3",
                "raw_log_path": f"{CANONICAL_DIR.as_posix()}/{name}-fixture.log",
            })
            for prefix in (
                "backend_cosign", "frontend_cosign", "backend_provenance",
                "frontend_provenance", "backend_sbom", "frontend_sbom",
            ):
                run[f"{prefix}_verification_sha256"] = shared_sha
                run[f"{prefix}_verification_path"] = (
                    f"{CANONICAL_DIR.as_posix()}/{prefix.replace('_', '-')}-fixture.json"
                )
            (r0_directory / f"{name}.json").write_text(
                json.dumps(run, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            (r0_directory / f"{name}-fixture.log").write_text("fixture log\n", encoding="utf-8")
        summary = generator.build_summary(
            root=root,
            evidence_dir=r0_directory,
            commit=commit,
            source_sha=context["source_sha"],
            registry_image=context["registry_image"],
            run_id="fixture",
        )
        summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        status_path.write_text(json.dumps({
            "result": "passed", "commit_sha": commit, "run_id": "fixture",
            "summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
        }), encoding="utf-8")
        llm_path.write_text(
            json.dumps(valid_llm_fixture(commit, backend, now)),
            encoding="utf-8",
        )
        unknown_provider = valid_llm_fixture(commit, backend, now)
        unknown_provider.update({
            "test_id": "GA-R0-001-LLM-deepseek",
            "provider_id": "not-a-provider",
            "provider_origin": "https://attacker.example/v1",
            "feature_flag": {"enabled_provider_id": "not-a-provider", "exclusive": True},
        })
        try:
            validate_llm(
                unknown_provider,
                release_digest=backend,
                commit=commit,
                now=now,
            )
        except EvidenceError:
            pass
        else:
            raise EvidenceError("self-test accepted an unknown LLM provider")
        bindings = {
            "matrix_summary_sha256": file_digest(summary_path),
            "release_manifest_sha256": file_digest(release_path),
            "llm_manifest_sha256": file_digest(llm_path),
        }
        allowed_signers_path = root / "protected-reviewers.allowed"
        allowed_signers_path.write_text(
            "architect ssh-ed25519 " + base64.b64encode(b"architect-key").decode() + "\n"
            "security ssh-ed25519 " + base64.b64encode(b"security-key").decode() + "\n",
            encoding="utf-8",
        )
        reviews: dict[str, Any] = {}
        for role, verdict, identity in (
            ("architecture", "CLEAR", "architect"),
            ("security", "APPROVE", "security"),
        ):
            statement_path = r0_directory / f"{role}-review.json"
            signature_path = r0_directory / f"{role}-review.sig"
            statement_path.write_text(json.dumps({
                "role": role,
                "verdict": verdict,
                "reviewer_identity": identity,
                "reviewed_at": now.isoformat(),
                "commit_sha": commit,
                "evidence_bindings": bindings,
            }), encoding="utf-8")
            signature_path.write_text("self-test signature\n", encoding="utf-8")
            reviews[role] = {
                "verdict": verdict,
                "reviewer_identity": identity,
                "reviewed_at": now.isoformat(),
                "statement_path": f"{CANONICAL_DIR.as_posix()}/{statement_path.name}",
                "signature_path": f"{CANONICAL_DIR.as_posix()}/{signature_path.name}",
            }
        review_path.write_text(json.dumps({
            "test_id": "GA-R0-001-REVIEW", "result": "passed", "commit_sha": commit,
            "reviews": reviews,
        }), encoding="utf-8")
        accept_test_signature = lambda *_args: True
        payload = assemble(
            root=root, commit=commit, matrix_summary_path=summary_path, matrix_status_path=status_path,
            release_manifest_path=release_path, llm_manifest_path=llm_path,
            review_manifest_path=review_path, expected_repository="example/repo", now=now,
            review_allowed_signers_path=allowed_signers_path,
            signature_verifier=accept_test_signature,
        )
        require(payload["release_digest"] == backend, "self-test digest mismatch")
        shared_key = base64.b64encode(b"shared-reviewer-key").decode()
        allowed_signers_path.write_text(
            f"architect ssh-ed25519 {shared_key}\nsecurity ssh-ed25519 {shared_key}\n",
            encoding="utf-8",
        )
        try:
            assemble(
                root=root,
                commit=commit,
                matrix_summary_path=summary_path,
                matrix_status_path=status_path,
                release_manifest_path=release_path,
                llm_manifest_path=llm_path,
                review_manifest_path=review_path,
                expected_repository="example/repo",
                review_allowed_signers_path=allowed_signers_path,
                now=now,
                signature_verifier=accept_test_signature,
            )
        except EvidenceError:
            pass
        else:
            raise EvidenceError("self-test accepted one reviewer key under two principals")
        allowed_signers_path.write_text(
            "architect ssh-ed25519 " + base64.b64encode(b"architect-key").decode() + "\n"
            "security ssh-ed25519 " + base64.b64encode(b"security-key").decode() + "\n",
            encoding="utf-8",
        )
        forged_llm = valid_llm_fixture(commit, backend, now)
        forged_llm["policy"]["security_owner"] = "ai-owner"
        forged_llm["policy"]["owner_approvals"]["security"][
            "approver_identity"
        ] = "ai-owner"
        llm_path.write_text(json.dumps(forged_llm), encoding="utf-8")
        try:
            assemble(
                root=root,
                commit=commit,
                matrix_summary_path=summary_path,
                matrix_status_path=status_path,
                release_manifest_path=release_path,
                llm_manifest_path=llm_path,
                review_manifest_path=review_path,
                expected_repository="example/repo",
                review_allowed_signers_path=allowed_signers_path,
                now=now,
                signature_verifier=accept_test_signature,
            )
        except EvidenceError:
            pass
        else:
            raise EvidenceError("self-test accepted forged owner approvals")
        llm_path.write_text(
            json.dumps(valid_llm_fixture(commit, backend, now)),
            encoding="utf-8",
        )
        stale_llm = valid_llm_fixture(commit, backend, now)
        stale_llm["expires_at"] = (now + timedelta(days=365)).isoformat()
        llm_path.write_text(json.dumps(stale_llm), encoding="utf-8")
        try:
            assemble(
                root=root,
                commit=commit,
                matrix_summary_path=summary_path,
                matrix_status_path=status_path,
                release_manifest_path=release_path,
                llm_manifest_path=llm_path,
                review_manifest_path=review_path,
                expected_repository="example/repo",
                review_allowed_signers_path=allowed_signers_path,
                now=now,
                signature_verifier=accept_test_signature,
            )
        except EvidenceError:
            pass
        else:
            raise EvidenceError("self-test accepted a provider manifest valid for more than 72 hours")
        llm_path.write_text(
            json.dumps(valid_llm_fixture(commit, backend, now)),
            encoding="utf-8",
        )
        broken = json.loads(summary_path.read_text())
        broken["artifact_class"] = "diagnostic"
        summary_path.write_text(json.dumps(broken), encoding="utf-8")
        try:
            assemble(
                root=root, commit=commit, matrix_summary_path=summary_path, matrix_status_path=status_path,
                release_manifest_path=release_path, llm_manifest_path=llm_path,
                review_manifest_path=review_path, expected_repository="example/repo", now=now,
                review_allowed_signers_path=allowed_signers_path,
                signature_verifier=accept_test_signature,
            )
        except EvidenceError:
            return
        raise EvidenceError("self-test accepted diagnostic matrix evidence")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit")
    parser.add_argument("--matrix-summary", type=Path)
    parser.add_argument("--matrix-status", type=Path)
    parser.add_argument("--release-manifest", type=Path)
    parser.add_argument("--llm-manifest", type=Path)
    parser.add_argument("--review-manifest", type=Path)
    parser.add_argument("--expected-repository")
    parser.add_argument("--review-allowed-signers", type=Path)
    parser.add_argument("--output", type=Path, default=CANONICAL_DIR / "manifest.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
        else:
            required = {
                "commit": args.commit, "matrix_summary": args.matrix_summary,
                "matrix_status": args.matrix_status, "release_manifest": args.release_manifest,
                "llm_manifest": args.llm_manifest, "review_manifest": args.review_manifest,
                "expected_repository": args.expected_repository,
                "review_allowed_signers": args.review_allowed_signers,
            }
            missing = [name for name, value in required.items() if value is None]
            if missing:
                parser.error(f"missing required arguments: {', '.join(missing)}")
            payload = assemble(
                root=Path.cwd(), commit=args.commit, matrix_summary_path=args.matrix_summary,
                matrix_status_path=args.matrix_status, release_manifest_path=args.release_manifest,
                llm_manifest_path=args.llm_manifest, review_manifest_path=args.review_manifest,
                expected_repository=args.expected_repository,
                review_allowed_signers_path=args.review_allowed_signers,
                now=datetime.now(timezone.utc),
            )
            write_atomically(args.output, payload)
    except EvidenceError as exc:
        print(f"R0 baseline manifest assembly failed: {exc}")
        return 1
    print("R0 baseline manifest assembly passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
