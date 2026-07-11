#!/usr/bin/env python3
"""Validate the blocking GA evidence manifest contract."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = (
    "test_id",
    "requirement",
    "automation",
    "environment",
    "fixture_or_seed",
    "sample_size_or_duration",
    "expected",
    "evidence_path",
    "owner",
    "release_digest",
    "result",
)
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
TEST_ID_PATTERN = re.compile(r"^GA-R[0-9]+(?:[A-Z])?-[0-9]{3}(?:-[A-Z0-9-]+)?$")
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
RELEASE_TAG_PATTERN = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")


class ManifestError(RuntimeError):
    pass


def validate_manifest(payload: Any, *, expected_repository: str | None = None) -> None:
    if not isinstance(payload, dict):
        raise ManifestError("manifest must be a JSON object")
    missing = [
        field
        for field in REQUIRED_FIELDS
        if not isinstance(payload.get(field), str) or not payload[field].strip()
    ]
    if missing:
        raise ManifestError(f"manifest is missing fixed fields: {', '.join(missing)}")
    test_id = payload["test_id"]
    if not TEST_ID_PATTERN.fullmatch(test_id):
        raise ManifestError("test_id is not a blocking GA identifier")
    expected_path = f".omx/evidence/production-ga/{test_id}/"
    if payload["evidence_path"] != expected_path:
        raise ManifestError(f"evidence_path must be {expected_path}")
    if not DIGEST_PATTERN.fullmatch(payload["release_digest"]):
        raise ManifestError("release_digest must use sha256:<64 lowercase hex>")
    if payload["result"] != "passed":
        raise ManifestError("blocking manifest result must be passed")
    release_digests = payload.get("release_digests")
    if test_id in {"GA-R0-001", "GA-R0-002"}:
        if not isinstance(release_digests, dict) or any(
            not DIGEST_PATTERN.fullmatch(str(release_digests.get(name) or ""))
            for name in ("backend", "frontend")
        ):
            raise ManifestError("release_digests must bind backend and frontend digests")
        if payload["release_digest"] != release_digests["backend"]:
            raise ManifestError("release_digest must equal release_digests.backend")
        if test_id == "GA-R0-002":
            repository = str(payload.get("repository") or "")
            if not REPOSITORY_PATTERN.fullmatch(repository):
                raise ManifestError("GA-R0-002 repository is invalid")
            if expected_repository is not None and repository != expected_repository:
                raise ManifestError("GA-R0-002 repository does not match the trusted repository")
            release_tag = str(payload.get("release_tag") or "")
            if not RELEASE_TAG_PATTERN.fullmatch(release_tag):
                raise ManifestError("GA-R0-002 release_tag must be semantic")
            if not re.fullmatch(r"[0-9a-f]{40}", str(payload.get("commit_sha") or "")):
                raise ManifestError("GA-R0-002 commit_sha must be a full Git SHA")
            expected_workflow_ref = (
                f"{repository}/.github/workflows/r0-ci.yml@refs/tags/{release_tag}"
            )
            if payload.get("workflow_ref") != expected_workflow_ref:
                raise ManifestError("GA-R0-002 workflow_ref is not bound to repository and tag")
            if payload.get("certificate_identity") != f"https://github.com/{expected_workflow_ref}":
                raise ManifestError("GA-R0-002 certificate_identity is not pinned to the release workflow")
            if payload.get("oidc_issuer") != "https://token.actions.githubusercontent.com":
                raise ManifestError("GA-R0-002 oidc_issuer is not the GitHub Actions issuer")
            owner = repository.split("/", 1)[0].lower()
            for name in ("backend", "frontend"):
                image = payload.get(name)
                expected_image = f"ghcr.io/{owner}/agent-ops-{name}"
                if (
                    not isinstance(image, dict)
                    or image.get("digest") != release_digests[name]
                    or image.get("image") != expected_image
                ):
                    raise ManifestError(
                        f"{name} image metadata must match the trusted GHCR namespace"
                    )
            tag_signature = payload.get("tag_signature")
            if (
                not isinstance(tag_signature, dict)
                or tag_signature.get("verified") is not True
                or tag_signature.get("format") != "ssh"
                or not DIGEST_PATTERN.fullmatch(
                    str(tag_signature.get("allowed_signers_sha256") or "")
                )
                or not DIGEST_PATTERN.fullmatch(
                    str(tag_signature.get("verification_log_sha256") or "")
                )
            ):
                raise ManifestError("GA-R0-002 trusted tag signature evidence is invalid")
        else:
            if not re.fullmatch(r"[0-9a-f]{40}", str(payload.get("commit_sha") or "")):
                raise ManifestError("GA-R0-001 commit_sha must be a full Git SHA")
            if not re.fullmatch(
                r"v[0-9]+\.[0-9]+\.[0-9]+", str(payload.get("release_tag") or "")
            ):
                raise ManifestError("GA-R0-001 release_tag must be semantic")
            evidence = payload.get("evidence")
            required_evidence = {
                "matrix_summary",
                "matrix_status",
                "release_manifest",
                "llm_manifest",
                "review_manifest",
                "architecture_review_statement",
                "architecture_review_signature",
                "security_review_statement",
                "security_review_signature",
            }
            if not isinstance(evidence, dict) or set(evidence) != required_evidence:
                raise ManifestError("GA-R0-001 evidence map is incomplete")
            for name, item in evidence.items():
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("path"), str)
                    or not item["path"].strip()
                    or not DIGEST_PATTERN.fullmatch(str(item.get("sha256") or ""))
                ):
                    raise ManifestError(f"GA-R0-001 evidence.{name} is invalid")
            trust_anchors = payload.get("trust_anchors")
            fingerprints = (
                trust_anchors.get("reviewer_key_fingerprints")
                if isinstance(trust_anchors, dict)
                else None
            )
            if (
                not isinstance(trust_anchors, dict)
                or not DIGEST_PATTERN.fullmatch(
                    str(trust_anchors.get("review_allowed_signers_sha256") or "")
                )
                or not isinstance(fingerprints, dict)
                or set(fingerprints) != {"architecture", "security"}
                or any(
                    not DIGEST_PATTERN.fullmatch(str(value or ""))
                    for value in fingerprints.values()
                )
                or len(set(fingerprints.values())) != 2
            ):
                raise ManifestError("GA-R0-001 external review trust anchors are invalid")
    elif release_digests is not None and not isinstance(release_digests, dict):
        raise ManifestError("release_digests must be an object when present")


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError("manifest must be readable JSON") from exc


def validate_g001_evidence_files(payload: dict[str, Any], *, root: Path) -> None:
    root = root.resolve()
    for name, item in payload["evidence"].items():
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ManifestError(f"GA-R0-001 evidence.{name} path is unsafe")
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ManifestError(f"GA-R0-001 evidence.{name} contains a symlink")
        if not current.is_file() or not current.resolve().is_relative_to(root):
            raise ManifestError(f"GA-R0-001 evidence.{name} is missing")
        actual = "sha256:" + hashlib.sha256(current.read_bytes()).hexdigest()
        if actual != item["sha256"]:
            raise ManifestError(f"GA-R0-001 evidence.{name} hash mismatch")


def verify_g001_signature(
    manifest_path: Path,
    *,
    signature_path: Path,
    allowed_signers_path: Path,
    signer_identity: str,
) -> str:
    if not signer_identity.strip():
        raise ManifestError("GA-R0-001 signer identity is empty")
    try:
        completed = subprocess.run(
            [
                "ssh-keygen", "-Y", "verify", "-f", str(allowed_signers_path),
                "-I", signer_identity, "-n", "aiops-r0-aggregate", "-s", str(signature_path),
            ],
            input=manifest_path.read_bytes(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError as exc:
        raise ManifestError("GA-R0-001 SSH signature verifier is unavailable") from exc
    if completed.returncode != 0:
        raise ManifestError("GA-R0-001 SSH signature is invalid")
    fingerprints: set[str] = set()
    for raw_line in allowed_signers_path.read_text(encoding="utf-8").splitlines():
        fields = raw_line.strip().split()
        if not fields or fields[0].startswith("#") or signer_identity not in fields[0].split(","):
            continue
        key_index = next(
            (
                index
                for index, value in enumerate(fields[1:], start=1)
                if value.startswith(("ssh-", "ecdsa-", "sk-"))
            ),
            None,
        )
        if key_index is None or key_index + 1 >= len(fields):
            raise ManifestError("GA-R0-001 signer has an invalid allowed-signers entry")
        try:
            key_blob = base64.b64decode(fields[key_index + 1], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ManifestError("GA-R0-001 signer has an invalid SSH public key") from exc
        fingerprints.add("sha256:" + hashlib.sha256(key_blob).hexdigest())
    if len(fingerprints) != 1:
        raise ManifestError("GA-R0-001 signer must resolve to exactly one trusted SSH key")
    return next(iter(fingerprints))


def _self_test() -> None:
    test_id = "GA-R0-002"
    payload = {field: "fixture" for field in REQUIRED_FIELDS}
    payload.update(
        {
            "test_id": test_id,
            "evidence_path": f".omx/evidence/production-ga/{test_id}/",
            "release_digest": "sha256:" + "a" * 64,
            "release_digests": {
                "backend": "sha256:" + "a" * 64,
                "frontend": "sha256:" + "b" * 64,
            },
            "backend": {
                "image": "ghcr.io/example/agent-ops-backend",
                "digest": "sha256:" + "a" * 64,
            },
            "frontend": {
                "image": "ghcr.io/example/agent-ops-frontend",
                "digest": "sha256:" + "b" * 64,
            },
            "repository": "example/repo",
            "release_tag": "v1.2.3",
            "commit_sha": "d" * 40,
            "workflow_ref": "example/repo/.github/workflows/r0-ci.yml@refs/tags/v1.2.3",
            "certificate_identity": "https://github.com/example/repo/.github/workflows/r0-ci.yml@refs/tags/v1.2.3",
            "oidc_issuer": "https://token.actions.githubusercontent.com",
            "tag_signature": {
                "verified": True,
                "format": "ssh",
                "allowed_signers_sha256": "sha256:" + "c" * 64,
                "verification_log_sha256": "sha256:" + "d" * 64,
            },
            "result": "passed",
        }
    )
    validate_manifest(payload)
    baseline = dict(payload)
    baseline.update(
        {
            "test_id": "GA-R0-001",
            "evidence_path": ".omx/evidence/production-ga/GA-R0-001/",
            "commit_sha": "d" * 40,
            "release_tag": "v1.2.3",
            "evidence": {
                name: {
                    "path": f"evidence/{name}.json",
                    "sha256": "sha256:" + "e" * 64,
                }
                for name in (
                    "matrix_summary",
                    "matrix_status",
                    "release_manifest",
                    "llm_manifest",
                    "review_manifest",
                    "architecture_review_statement",
                    "architecture_review_signature",
                    "security_review_statement",
                    "security_review_signature",
                )
            },
            "trust_anchors": {
                "review_allowed_signers_sha256": "sha256:" + "a" * 64,
                "reviewer_key_fingerprints": {
                    "architecture": "sha256:" + "b" * 64,
                    "security": "sha256:" + "c" * 64,
                },
            },
        }
    )
    baseline.pop("backend")
    baseline.pop("frontend")
    validate_manifest(baseline)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "manifest.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        validate_manifest(_load(path))
    invalid_payloads = []
    invalid_path = dict(payload)
    invalid_path["evidence_path"] = "evidence/release/"
    invalid_payloads.append(invalid_path)
    missing_digest_map = dict(payload)
    missing_digest_map.pop("release_digests")
    invalid_payloads.append(missing_digest_map)
    mismatched_digest = json.loads(json.dumps(payload))
    mismatched_digest["release_digests"]["backend"] = "sha256:" + "c" * 64
    invalid_payloads.append(mismatched_digest)
    mismatched_nested = json.loads(json.dumps(payload))
    mismatched_nested["frontend"]["digest"] = "sha256:" + "c" * 64
    invalid_payloads.append(mismatched_nested)
    for invalid in invalid_payloads:
        try:
            validate_manifest(invalid)
        except ManifestError:
            continue
        raise ManifestError("self-test accepted an invalid release manifest")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", nargs="?", type=Path)
    parser.add_argument("--expected-repository")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--signature", type=Path)
    parser.add_argument("--allowed-signers", type=Path)
    parser.add_argument("--signer-identity")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            _self_test()
        elif args.manifest is None:
            parser.error("manifest path is required unless --self-test is used")
        else:
            payload = _load(args.manifest)
            validate_manifest(
                payload,
                expected_repository=args.expected_repository,
            )
            if payload.get("test_id") == "GA-R0-001":
                if any(
                    value is None
                    for value in (
                        args.root,
                        args.signature,
                        args.allowed_signers,
                        args.signer_identity,
                    )
                ):
                    raise ManifestError(
                        "GA-R0-001 verification requires --root, --signature, "
                        "--allowed-signers and --signer-identity"
                    )
                validate_g001_evidence_files(payload, root=args.root)
                aggregate_fingerprint = verify_g001_signature(
                    args.manifest,
                    signature_path=args.signature,
                    allowed_signers_path=args.allowed_signers,
                    signer_identity=args.signer_identity,
                )
                reviewer_fingerprints = set(
                    payload["trust_anchors"]["reviewer_key_fingerprints"].values()
                )
                if aggregate_fingerprint in reviewer_fingerprints:
                    raise ManifestError(
                        "GA-R0-001 aggregate signer must be independent of both reviewers"
                    )
    except ManifestError as exc:
        print(f"GA manifest validation failed: {exc}")
        return 1
    print("GA manifest validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
