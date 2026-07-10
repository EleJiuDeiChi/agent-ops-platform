#!/usr/bin/env python3
"""Validate the signed release attestations beyond signature existence."""

from __future__ import annotations

import argparse
import base64
import json
import re
from pathlib import Path
from typing import Any


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
GITHUB_WORKFLOW_BUILD_TYPE = "https://actions.github.io/buildtypes/workflow/v1"
GITHUB_HOSTED_BUILDER = "https://github.com/actions/runner/github-hosted"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


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


def has_subject(statement: dict[str, Any], image_name: str, digest: str) -> bool:
    for subject in statement.get("subject") or []:
        if (
            isinstance(subject, dict)
            and subject.get("name") == image_name
            and (subject.get("digest") or {}).get("sha256") == digest
        ):
            return True
    return False


def validate_common(
    statement: dict[str, Any], image_name: str, digest: str, predicate_type: str
) -> None:
    require(statement.get("_type") in IN_TOTO_STATEMENTS, "attestation has an unsupported in-toto statement version")
    require(statement.get("predicateType") == predicate_type, f"unexpected predicate type: {statement.get('predicateType')!r}")
    require(has_subject(statement, image_name, digest), "attestation subject name/digest does not match the image")
    require(isinstance(statement.get("predicate"), dict), "attestation predicate must be an object")


def validate_provenance(
    statements: list[dict[str, Any]],
    *,
    image_name: str,
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
            validate_common(statement, image_name, digest, str(statement["predicateType"]))
            predicate = statement["predicate"]
            build_definition = predicate.get("buildDefinition") or {}
            require(
                build_definition.get("buildType") == GITHUB_WORKFLOW_BUILD_TYPE,
                "provenance buildType is not the GitHub workflow v1 schema",
            )
            workflow = ((build_definition.get("externalParameters") or {}).get("workflow") or {})
            require(isinstance(workflow, dict), "provenance workflow parameters are missing")
            require(
                workflow.get("repository") == f"https://github.com/{repository}",
                "provenance is not bound to the release repository",
            )
            require(workflow.get("ref") == ref, "provenance is not bound to the semantic release ref")
            require(
                workflow.get("path") == ".github/workflows/r0-ci.yml",
                "provenance is not bound to the reviewed release workflow",
            )
            dependencies = build_definition.get("resolvedDependencies") or []
            expected_prefix = f"git+https://github.com/{repository}"
            require(
                any(
                    isinstance(item, dict)
                    and str(item.get("uri") or "").startswith(expected_prefix)
                    and (item.get("digest") or {}).get("gitCommit") == commit
                    for item in dependencies
                ),
                "provenance resolvedDependencies are not bound to the reviewed Git commit",
            )
            builder = (predicate.get("runDetails") or {}).get("builder") or {}
            require(
                builder.get("id") == GITHUB_HOSTED_BUILDER,
                "provenance builder is not the reviewed GitHub-hosted runner",
            )
            return
        except (KeyError, ValueError) as exc:
            errors.append(str(exc))
    detail = "; ".join(errors) if errors else "no SLSA provenance statement found"
    raise ValueError(f"no provenance statement satisfied release policy: {detail}")


def validate_spdx(statements: list[dict[str, Any]], *, image_name: str, digest: str) -> None:
    errors: list[str] = []
    for statement in statements:
        if statement.get("predicateType") != SPDX_TYPE:
            continue
        try:
            validate_common(statement, image_name, digest, SPDX_TYPE)
            predicate = statement["predicate"]
            require(
                bool(re.fullmatch(r"SPDX-2\.[0-9]+", str(predicate.get("spdxVersion") or ""))),
                "SPDX predicate has an invalid spdxVersion",
            )
            require(predicate.get("SPDXID") == "SPDXRef-DOCUMENT", "SPDX predicate is not a document")
            require(bool(predicate.get("name")), "SPDX document name is missing")
            require(predicate.get("dataLicense") == "CC0-1.0", "SPDX document data license drifted")
            packages = predicate.get("packages")
            require(isinstance(packages, list) and bool(packages), "SPDX predicate must describe packages")
            require(
                all(isinstance(item, dict) and bool(item.get("SPDXID")) and bool(item.get("name")) for item in packages),
                "SPDX predicate contains an empty package record",
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
    image_name = image_ref.rsplit("@", 1)[0]
    repository = identity_match.group("repository")
    ref = identity_match.group("ref")
    validate_provenance(
        load_statements(provenance_path),
        image_name=image_name,
        digest=digest,
        commit=commit,
        repository=repository,
        ref=ref,
    )
    validate_spdx(load_statements(sbom_path), image_name=image_name, digest=digest)


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
                "buildType": GITHUB_WORKFLOW_BUILD_TYPE,
                "externalParameters": {
                    "workflow": {
                        "repository": f"https://github.com/{repository}",
                        "ref": ref,
                        "path": ".github/workflows/r0-ci.yml",
                    },
                },
                "resolvedDependencies": [
                    {
                        "uri": f"git+https://github.com/{repository}@{ref}",
                        "digest": {"gitCommit": commit},
                    }
                ],
            },
            "runDetails": {"builder": {"id": GITHUB_HOSTED_BUILDER}},
        },
    }
    spdx = {
        **common,
        "predicateType": SPDX_TYPE,
        "predicate": {
            "spdxVersion": "SPDX-2.3",
            "SPDXID": "SPDXRef-DOCUMENT",
            "name": "ghcr.io/owner/image",
            "dataLicense": "CC0-1.0",
            "packages": [{"SPDXID": "SPDXRef-Package-app", "name": "application"}],
        },
    }
    envelope = {"payload": base64.b64encode(json.dumps(provenance).encode()).decode()}
    require(decode_statement(envelope) == provenance, "DSSE payload decoding changed the statement")
    validate_provenance(
        [provenance],
        image_name="ghcr.io/owner/image",
        digest=digest,
        commit=commit,
        repository=repository,
        ref=ref,
    )
    validate_spdx([spdx], image_name="ghcr.io/owner/image", digest=digest)

    def expect_rejected(callback: Any, label: str) -> None:
        try:
            callback()
        except ValueError:
            return
        raise AssertionError(f"semantic verifier accepted {label}")

    padded = json.loads(json.dumps(provenance))
    padded["subject"][0]["name"] = "ghcr.io/attacker/unrelated"
    padded_build = padded["predicate"]["buildDefinition"]
    padded_build["externalParameters"]["workflow"] = {
        "repository": "https://github.com/attacker/repository",
        "ref": "refs/tags/v9.9.9",
        "path": ".github/workflows/evil.yml",
    }
    padded_build["resolvedDependencies"] = [
        {"uri": "git+https://github.com/attacker/repository", "digest": {"gitCommit": "3" * 40}}
    ]
    padded["predicate"]["runDetails"]["builder"]["id"] = "https://attacker.invalid/builder"
    padded["predicate"]["untrustedNote"] = f"{repository} {ref} {commit} buildkit"
    expect_rejected(
        lambda: validate_provenance(
            [padded],
            image_name="ghcr.io/owner/image",
            digest=digest,
            commit=commit,
            repository=repository,
            ref=ref,
        ),
        "provenance padded with correct values in an unrelated field",
    )

    empty_spdx = json.loads(json.dumps(spdx))
    empty_spdx["predicate"]["packages"] = [{}]
    expect_rejected(
        lambda: validate_spdx([empty_spdx], image_name="ghcr.io/owner/image", digest=digest),
        "SPDX document with an empty package",
    )


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
