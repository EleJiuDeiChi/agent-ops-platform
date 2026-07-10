#!/usr/bin/env python3
"""Validate the blocking GA evidence manifest contract."""

from __future__ import annotations

import argparse
import json
import re
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


class ManifestError(RuntimeError):
    pass


def validate_manifest(payload: Any) -> None:
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
    if test_id == "GA-R0-002":
        if not isinstance(release_digests, dict) or any(
            not DIGEST_PATTERN.fullmatch(str(release_digests.get(name) or ""))
            for name in ("backend", "frontend")
        ):
            raise ManifestError("release_digests must bind backend and frontend digests")
        if payload["release_digest"] != release_digests["backend"]:
            raise ManifestError("release_digest must equal release_digests.backend")
        for name in ("backend", "frontend"):
            image = payload.get(name)
            if (
                not isinstance(image, dict)
                or image.get("digest") != release_digests[name]
                or not isinstance(image.get("image"), str)
                or not image["image"].strip()
            ):
                raise ManifestError(
                    f"{name} image metadata must match release_digests.{name}"
                )
    elif release_digests is not None and not isinstance(release_digests, dict):
        raise ManifestError("release_digests must be an object when present")


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError("manifest must be readable JSON") from exc


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
                "image": "ghcr.io/example/backend",
                "digest": "sha256:" + "a" * 64,
            },
            "frontend": {
                "image": "ghcr.io/example/frontend",
                "digest": "sha256:" + "b" * 64,
            },
            "result": "passed",
        }
    )
    validate_manifest(payload)
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
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            _self_test()
        elif args.manifest is None:
            parser.error("manifest path is required unless --self-test is used")
        else:
            validate_manifest(_load(args.manifest))
    except ManifestError as exc:
        print(f"GA manifest validation failed: {exc}")
        return 1
    print("GA manifest validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
