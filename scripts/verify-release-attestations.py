#!/usr/bin/env python3
"""Validate the signed release attestations beyond signature existence."""

from __future__ import annotations

import argparse
import base64
import json
import re
from pathlib import Path
from typing import Any, Iterable


IMAGE_PATTERN = re.compile(r"^[^@\s]+@sha256:(?P<digest>[0-9a-f]{64})$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
IDENTITY_PATTERN = re.compile(
    r"^https://github\.com/(?P<repository>[^/\s]+/[^/\s]+)/"
    r"\.github/workflows/r0-ci\.yml@(?P<ref>refs/tags/v[0-9]+\.[0-9]+\.[0-9]+)$"
)
IN_TOTO_STATEMENTS = {
    "https://in-toto.io/Statement/v1",
    "https://in-toto.io/Statement/v0.1",
}
PROVENANCE_TYPES = {
    "https://slsa.dev/provenance/v1",
    "https://slsa.dev/provenance/v0.2",
}
SPDX_TYPE = "https://spdx.dev/Document"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def iter_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_strings(item)


def decode_statement(row: Any) -> dict[str, Any]:
    require(isinstance(row, dict), "attestation row must be a JSON object")
    if "predicateType" in row:
        return row
    envelope = row.get("attestation") if isinstance(row.get("attestation"), dict) else row
    payload = envelope.get("payload")
    require(isinstance(payload, str) and payload, "verified attestation is missing a DSSE payload")
    try:
        decoded = base64.b64decode(payload, validate=True)
        statement = json.loads(decoded)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("verified attestation contains an invalid DSSE payload") from exc
    require(isinstance(statement, dict), "decoded attestation statement must be an object")
    return statement


def load_statements(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    require(bool(text), f"attestation output is empty: {path}")
    try:
        payload = json.loads(text)
        rows = payload if isinstance(payload, list) else [payload]
    except json.JSONDecodeError:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    statements = [decode_statement(row) for row in rows]
    require(bool(statements), f"attestation output has no statements: {path}")
    return statements


def has_subject(statement: dict[str, Any], digest: str) -> bool:
    for subject in statement.get("subject") or []:
        if isinstance(subject, dict) and (subject.get("digest") or {}).get("sha256") == digest:
            return True
    return False


def validate_common(statement: dict[str, Any], digest: str, predicate_type: str) -> None:
    require(statement.get("_type") in IN_TOTO_STATEMENTS, "attestation has an unsupported in-toto statement version")
    require(statement.get("predicateType") == predicate_type, f"unexpected predicate type: {statement.get('predicateType')!r}")
    require(has_subject(statement, digest), "attestation subject does not match the image digest")
    require(isinstance(statement.get("predicate"), dict), "attestation predicate must be an object")


def validate_provenance(
    statements: list[dict[str, Any]],
    *,
    digest: str,
    commit: str,
    repository: str,
    ref: str,
) -> None:
    errors: list[str] = []
    for statement in statements:
        if statement.get("predicateType") not in PROVENANCE_TYPES:
            continue
        try:
            validate_common(statement, digest, str(statement["predicateType"]))
            strings = list(iter_strings(statement["predicate"]))
            lowered = [item.lower() for item in strings]
            require(any(commit in item for item in strings), "provenance is not bound to the reviewed commit")
            require(
                any(repository.lower() in item for item in lowered),
                "provenance is not bound to the release repository",
            )
            require(any(ref in item for item in strings), "provenance is not bound to the semantic release ref")
            require(
                any("buildkit" in item or "github.com/actions/runner" in item for item in lowered),
                "provenance builder is outside the allowed BuildKit/GitHub runner boundary",
            )
            return
        except (KeyError, ValueError) as exc:
            errors.append(str(exc))
    detail = "; ".join(errors) if errors else "no SLSA provenance statement found"
    raise ValueError(f"no provenance statement satisfied release policy: {detail}")


def validate_spdx(statements: list[dict[str, Any]], *, digest: str) -> None:
    errors: list[str] = []
    for statement in statements:
        if statement.get("predicateType") != SPDX_TYPE:
            continue
        try:
            validate_common(statement, digest, SPDX_TYPE)
            predicate = statement["predicate"]
            require(
                bool(re.fullmatch(r"SPDX-2\.[0-9]+", str(predicate.get("spdxVersion") or ""))),
                "SPDX predicate has an invalid spdxVersion",
            )
            require(
                isinstance(predicate.get("packages"), list) and bool(predicate["packages"]),
                "SPDX predicate must describe at least one package",
            )
            return
        except ValueError as exc:
            errors.append(str(exc))
    detail = "; ".join(errors) if errors else "no SPDX statement found"
    raise ValueError(f"no SPDX statement satisfied release policy: {detail}")


def validate(
    provenance_path: Path,
    sbom_path: Path,
    image_ref: str,
    commit: str,
    certificate_identity: str,
) -> None:
    image_match = IMAGE_PATTERN.fullmatch(image_ref)
    require(bool(image_match), "image reference must be a registry reference pinned by SHA-256")
    require(bool(COMMIT_PATTERN.fullmatch(commit)), "reviewed commit must be a full lowercase SHA-1")
    identity_match = IDENTITY_PATTERN.fullmatch(certificate_identity)
    require(bool(identity_match), "certificate identity must pin r0-ci.yml to an immutable semantic tag")
    digest = image_match.group("digest")
    repository = identity_match.group("repository")
    ref = identity_match.group("ref")
    validate_provenance(
        load_statements(provenance_path),
        digest=digest,
        commit=commit,
        repository=repository,
        ref=ref,
    )
    validate_spdx(load_statements(sbom_path), digest=digest)


def self_test() -> None:
    digest = "1" * 64
    commit = "2" * 40
    repository = "owner/repository"
    ref = "refs/tags/v1.2.3"
    common = {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [{"name": "ghcr.io/owner/image", "digest": {"sha256": digest}}],
    }
    provenance = {
        **common,
        "predicateType": "https://slsa.dev/provenance/v1",
        "predicate": {
            "buildDefinition": {
                "buildType": "https://mobyproject.org/buildkit@v1",
                "externalParameters": {
                    "repository": f"https://github.com/{repository}",
                    "ref": ref,
                    "commit": commit,
                },
            },
            "runDetails": {"builder": {"id": "https://github.com/actions/runner/github-hosted"}},
        },
    }
    spdx = {
        **common,
        "predicateType": SPDX_TYPE,
        "predicate": {"spdxVersion": "SPDX-2.3", "packages": [{"name": "application"}]},
    }
    envelope = {"payload": base64.b64encode(json.dumps(provenance).encode()).decode()}
    require(decode_statement(envelope) == provenance, "DSSE payload decoding changed the statement")
    validate_provenance([provenance], digest=digest, commit=commit, repository=repository, ref=ref)
    validate_spdx([spdx], digest=digest)
    invalid = json.loads(json.dumps(provenance))
    invalid["predicate"]["buildDefinition"]["externalParameters"]["commit"] = "3" * 40
    try:
        validate_provenance([invalid], digest=digest, commit=commit, repository=repository, ref=ref)
    except ValueError:
        return
    raise AssertionError("semantic verifier accepted provenance for the wrong commit")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provenance", type=Path)
    parser.add_argument("--sbom", type=Path)
    parser.add_argument("--image-ref")
    parser.add_argument("--commit-sha")
    parser.add_argument("--certificate-identity")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        require(
            all((args.provenance, args.sbom, args.image_ref, args.commit_sha, args.certificate_identity)),
            "all attestation verification arguments are required",
        )
        validate(
            args.provenance,
            args.sbom,
            args.image_ref,
            args.commit_sha,
            args.certificate_identity,
        )
        print(f"release attestation semantics verified for {args.image_ref}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
