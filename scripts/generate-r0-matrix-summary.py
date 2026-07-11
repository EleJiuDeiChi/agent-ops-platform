#!/usr/bin/env python3
"""Validate the four clean-VM manifests and atomically publish their summary."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EVIDENCE_PREFIX = ".omx/evidence/production-ga/GA-R0-001"
EXPECTED_VARIANTS = {
    "agent-ops-r0-ubuntu2204-ext4": ("22.04", "ext4"),
    "agent-ops-r0-ubuntu2204-xfs": ("22.04", "xfs"),
    "agent-ops-r0-ubuntu2404-ext4": ("24.04", "ext4"),
    "agent-ops-r0-ubuntu2404-xfs": ("24.04", "xfs"),
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
OCI_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
PINNED_IMAGE_RE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
VOLUME_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class ValidationError(RuntimeError):
    """Raised when evidence cannot support a passed matrix summary."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def validate_runs(
    runs_by_name: dict[str, dict[str, Any]],
    *,
    commit: str,
    source_sha: str,
    registry_image: str,
    run_id: str,
) -> list[dict[str, Any]]:
    require(set(runs_by_name) == set(EXPECTED_VARIANTS), "matrix must contain exactly four named variants")
    require(bool(re.fullmatch(r"[0-9a-f]{40}", commit)), "commit must be a full lowercase Git SHA")
    require(bool(SHA256_RE.fullmatch(source_sha)), "source archive SHA-256 is invalid")
    require(bool(PINNED_IMAGE_RE.fullmatch(registry_image)), "registry image is not pinned by digest")
    require(bool(run_id), "run ID is empty")

    ordered: list[dict[str, Any]] = []
    for name, expected_variant in EXPECTED_VARIANTS.items():
        run = runs_by_name[name]
        require(run.get("result") == "passed", f"{name}: result is not passed")
        require(run.get("artifact_class") == "signed_release", f"{name}: artifact is not a signed release")
        require(run.get("commit_sha") == commit, f"{name}: commit mismatch")
        require(run.get("source_archive_sha256") == source_sha, f"{name}: source archive mismatch")
        require(run.get("registry_image") == registry_image, f"{name}: registry image mismatch")
        require(run.get("evidence_name") == name, f"{name}: evidence-name mismatch")
        require(
            (run.get("os_version"), run.get("data_filesystem")) == expected_variant,
            f"{name}: OS/filesystem variant mismatch",
        )
        require(run.get("filesystem") == run.get("data_filesystem"), f"{name}: filesystem alias mismatch")
        require(run.get("root_filesystem") in {"ext4", "xfs"}, f"{name}: unsupported root filesystem")
        require(run.get("docker_root_dir") == "/var/lib/docker", f"{name}: unexpected Docker root")
        require(
            run.get("docker_storage_driver") in {"overlay2", "overlayfs"},
            f"{name}: unsupported Docker storage driver",
        )
        require(bool(SHA256_RE.fullmatch(str(run.get("cloud_image_sha256", "")))), f"{name}: cloud-image SHA-256 invalid")
        require(str(run.get("cloud_image_url", "")).startswith("https://cloud-images.ubuntu.com/"), f"{name}: cloud-image URL invalid")
        require(
            run.get("evidence_path") == f"{EVIDENCE_PREFIX}/{name}.json",
            f"{name}: evidence path mismatch",
        )
        require(
            run.get("raw_log_path") == f"{EVIDENCE_PREFIX}/{name}-{run_id}.log",
            f"{name}: raw-log path mismatch",
        )
        require(bool(OCI_DIGEST_RE.fullmatch(str(run.get("release_digest", "")))), f"{name}: backend digest invalid")
        require(
            bool(OCI_DIGEST_RE.fullmatch(str(run.get("frontend_release_digest", "")))),
            f"{name}: frontend digest invalid",
        )
        require(
            run.get("upstream_backend_image_ref", "").endswith(f"@{run['release_digest']}"),
            f"{name}: upstream backend reference does not match the release digest",
        )
        require(
            run.get("upstream_frontend_image_ref", "").endswith(
                f"@{run['frontend_release_digest']}"
            ),
            f"{name}: upstream frontend reference does not match the release digest",
        )
        require(
            bool(OCI_DIGEST_RE.fullmatch(str(run.get("release_manifest_sha256", "")))),
            f"{name}: release manifest digest is invalid",
        )
        require(
            run.get("release_manifest_path")
            == f"{EVIDENCE_PREFIX}/release-manifest-{run_id}.json",
            f"{name}: release manifest path mismatch",
        )
        require(run.get("release_tag_signature_verified") is True, f"{name}: release tag signature is unverified")
        for field in ("backend_cosign_verification_sha256", "frontend_cosign_verification_sha256"):
            require(
                bool(OCI_DIGEST_RE.fullmatch(str(run.get(field, "")))),
                f"{name}: {field} is invalid",
            )
        for image_name in ("backend", "frontend"):
            path_field = f"{image_name}_cosign_verification_path"
            expected = f"{EVIDENCE_PREFIX}/{image_name}-cosign-{run_id}.json"
            require(run.get(path_field) == expected, f"{name}: {path_field} mismatch")
            for evidence_kind in ("provenance", "sbom"):
                path_field = f"{image_name}_{evidence_kind}_verification_path"
                digest_field = f"{image_name}_{evidence_kind}_verification_sha256"
                expected = (
                    f"{EVIDENCE_PREFIX}/{image_name}-{evidence_kind}-{run_id}.json"
                )
                require(run.get(path_field) == expected, f"{name}: {path_field} mismatch")
                require(
                    bool(OCI_DIGEST_RE.fullmatch(str(run.get(digest_field, "")))),
                    f"{name}: {digest_field} is invalid",
                )

        sqlite = run.get("sqlite_storage")
        require(isinstance(sqlite, dict), f"{name}: SQLite storage evidence is missing")
        require(sqlite.get("sqlite_present") is True, f"{name}: SQLite database was not proven present")
        require(sqlite.get("quick_check") == "ok", f"{name}: SQLite quick_check was not proven successful")
        require(sqlite.get("filesystem") == run.get("data_filesystem"), f"{name}: SQLite filesystem mismatch")
        volume_name = str(sqlite.get("volume_name", ""))
        require(bool(VOLUME_NAME_RE.fullmatch(volume_name)), f"{name}: SQLite volume name is invalid")
        mountpoint = str(sqlite.get("volume_mountpoint", ""))
        require(
            mountpoint == f"/var/lib/docker/volumes/{volume_name}/_data",
            f"{name}: SQLite volume mountpoint is outside Docker's data root",
        )
        ordered.append(run)

    require(len({run["release_digest"] for run in ordered}) == 1, "backend OCI digests differ across variants")
    require(
        len({run["frontend_release_digest"] for run in ordered}) == 1,
        "frontend OCI digests differ across variants",
    )
    require(
        len({run["release_manifest_sha256"] for run in ordered}) == 1,
        "release manifest differs across variants",
    )
    for os_version in ("22.04", "24.04"):
        os_runs = [run for run in ordered if run["os_version"] == os_version]
        require(len({run["cloud_image_sha256"] for run in os_runs}) == 1, f"Ubuntu {os_version} fixture hashes differ")
        require(len({run["cloud_image_url"] for run in os_runs}) == 1, f"Ubuntu {os_version} fixture URLs differ")
    return ordered


def evidence_hashes(root: Path, runs: list[dict[str, Any]]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for run in runs:
        for key in (
            "evidence_path",
            "raw_log_path",
            "release_manifest_path",
            "backend_cosign_verification_path",
            "frontend_cosign_verification_path",
            "backend_provenance_verification_path",
            "frontend_provenance_verification_path",
            "backend_sbom_verification_path",
            "frontend_sbom_verification_path",
        ):
            relative = Path(run[key])
            require(not relative.is_absolute() and ".." not in relative.parts, f"unsafe evidence path: {relative}")
            path = verified_artifact_path(root, relative)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            hashes[str(relative)] = digest
            if key.endswith("_verification_path"):
                prefix = key.removesuffix("_verification_path")
                require(
                    run[f"{prefix}_verification_sha256"]
                    == f"sha256:{digest}",
                    f"{run['evidence_name']}: {prefix} evidence hash mismatch",
                )
            elif key == "release_manifest_path":
                require(
                    run["release_manifest_sha256"] == f"sha256:{digest}",
                    f"{run['evidence_name']}: release manifest evidence hash mismatch",
                )
    return hashes


def require_no_symlink_components(root: Path, relative: Path) -> Path:
    require(not relative.is_absolute() and ".." not in relative.parts, f"unsafe repository path: {relative}")
    current = root
    for part in relative.parts:
        current = current / part
        require(not current.is_symlink(), f"repository path contains a symlink: {relative}")
    return current


def verified_artifact_path(root: Path, relative: Path) -> Path:
    current = require_no_symlink_components(root, relative)
    require(current.is_file(), f"evidence artifact is missing: {relative}")
    require(current.resolve().is_relative_to(root.resolve()), f"evidence artifact escapes the repository: {relative}")
    return current


def build_summary(
    *,
    root: Path,
    evidence_dir: Path,
    commit: str,
    source_sha: str,
    registry_image: str,
    run_id: str,
) -> dict[str, Any]:
    expected_evidence_dir = root / EVIDENCE_PREFIX
    require(evidence_dir == expected_evidence_dir, "evidence directory is outside the fixed repository path")
    require_no_symlink_components(root, Path(EVIDENCE_PREFIX))
    runs_by_name = {
        name: json.loads(
            verified_artifact_path(root, Path(EVIDENCE_PREFIX) / f"{name}.json").read_text(encoding="utf-8")
        )
        for name in EXPECTED_VARIANTS
    }
    runs = validate_runs(
        runs_by_name,
        commit=commit,
        source_sha=source_sha,
        registry_image=registry_image,
        run_id=run_id,
    )
    return {
        "test_id": "GA-R0-001",
        "requirement": "production fail-closed and clean Ubuntu 22.04/24.04 x ext4/xfs matrix on identical OCI manifests",
        "automation": "scripts/test-r0-clean-vm-matrix.sh",
        "environment": [run["environment"] for run in runs],
        "fixture_or_seed": "GPG-verified Ubuntu cloud images and exact commit archive",
        "sample_size_or_duration": "4 clean VMs; full backend suite and one production Compose lifecycle per VM",
        "expected": "all four OS/filesystem combinations pass with the same backend/frontend OCI digests",
        "evidence_path": f"{EVIDENCE_PREFIX}/matrix-summary.json",
        "owner": "backend and release leads",
        "release_digest": runs[0]["release_digest"],
        "frontend_release_digest": runs[0]["frontend_release_digest"],
        "artifact_class": "signed_release",
        "upstream_backend_image_ref": runs[0]["upstream_backend_image_ref"],
        "upstream_frontend_image_ref": runs[0]["upstream_frontend_image_ref"],
        "release_manifest_sha256": runs[0]["release_manifest_sha256"],
        "release_manifest_path": runs[0]["release_manifest_path"],
        "release_tag": runs[0]["release_tag"],
        "result": "passed",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "commit_sha": commit,
        "source_archive_sha256": source_sha,
        "registry_image": registry_image,
        "run_id": run_id,
        "evidence_sha256": evidence_hashes(root, runs),
        "runs": runs,
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


def invalidate_summary(evidence_dir: Path, run_id: str) -> None:
    (evidence_dir / "matrix-summary.json").unlink(missing_ok=True)
    for pending in evidence_dir.glob(".matrix-*.pending"):
        pending.unlink()
    write_atomically(
        evidence_dir / "matrix-status.json",
        {
            "result": "in_progress",
            "run_id": run_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def publish_summary(evidence_dir: Path, payload: dict[str, Any], *, run_id: str, commit: str) -> None:
    summary_path = evidence_dir / "matrix-summary.json"
    write_atomically(summary_path, payload)
    try:
        write_atomically(
            evidence_dir / "matrix-status.json",
            {
                "result": "passed",
                "run_id": run_id,
                "commit_sha": commit,
                "summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:
        summary_path.unlink(missing_ok=True)
        raise


def stage_summary(evidence_dir: Path, payload: dict[str, Any], *, run_id: str, commit: str) -> None:
    summary_path = evidence_dir / f".matrix-summary.{run_id}.pending"
    status_path = evidence_dir / f".matrix-status.{run_id}.pending"
    write_atomically(summary_path, payload)
    try:
        write_atomically(
            status_path,
            {
                "result": "passed",
                "run_id": run_id,
                "commit_sha": commit,
                "summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:
        summary_path.unlink(missing_ok=True)
        raise


def synthetic_runs() -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    commit = "a" * 40
    source_sha = "b" * 64
    registry_image = "registry@sha256:" + "c" * 64
    run_id = "self-test"
    runs: dict[str, dict[str, Any]] = {}
    for name, (os_version, filesystem) in EXPECTED_VARIANTS.items():
        runs[name] = {
            "result": "passed",
            "environment": f"Ubuntu {os_version} x86_64 clean KVM VM with {filesystem} Docker/SQLite data",
            "commit_sha": commit,
            "source_archive_sha256": source_sha,
            "registry_image": registry_image,
            "evidence_name": name,
            "os_version": os_version,
            "data_filesystem": filesystem,
            "filesystem": filesystem,
            "root_filesystem": "ext4",
            "docker_root_dir": "/var/lib/docker",
            "docker_storage_driver": "overlay2",
            "cloud_image_sha256": "1" * 64 if os_version == "22.04" else "2" * 64,
            "cloud_image_url": f"https://cloud-images.ubuntu.com/{'jammy' if os_version == '22.04' else 'noble'}/current/test.img",
            "evidence_path": f"{EVIDENCE_PREFIX}/{name}.json",
            "raw_log_path": f"{EVIDENCE_PREFIX}/{name}-{run_id}.log",
            "release_digest": "sha256:" + "d" * 64,
            "frontend_release_digest": "sha256:" + "e" * 64,
            "artifact_class": "signed_release",
            "upstream_backend_image_ref": "ghcr.io/example/backend@sha256:" + "d" * 64,
            "upstream_frontend_image_ref": "ghcr.io/example/frontend@sha256:" + "e" * 64,
            "release_manifest_sha256": "sha256:" + "f" * 64,
            "release_manifest_path": f"{EVIDENCE_PREFIX}/release-manifest-{run_id}.json",
            "release_tag": "v1.2.3",
            "release_tag_signature_verified": True,
            "backend_cosign_verification_sha256": "sha256:" + "6" * 64,
            "frontend_cosign_verification_sha256": "sha256:" + "7" * 64,
            "backend_cosign_verification_path": f"{EVIDENCE_PREFIX}/backend-cosign-{run_id}.json",
            "frontend_cosign_verification_path": f"{EVIDENCE_PREFIX}/frontend-cosign-{run_id}.json",
            "backend_provenance_verification_sha256": "sha256:" + "8" * 64,
            "frontend_provenance_verification_sha256": "sha256:" + "9" * 64,
            "backend_sbom_verification_sha256": "sha256:" + "a" * 64,
            "frontend_sbom_verification_sha256": "sha256:" + "b" * 64,
            "backend_provenance_verification_path": f"{EVIDENCE_PREFIX}/backend-provenance-{run_id}.json",
            "frontend_provenance_verification_path": f"{EVIDENCE_PREFIX}/frontend-provenance-{run_id}.json",
            "backend_sbom_verification_path": f"{EVIDENCE_PREFIX}/backend-sbom-{run_id}.json",
            "frontend_sbom_verification_path": f"{EVIDENCE_PREFIX}/frontend-sbom-{run_id}.json",
            "sqlite_storage": {
                "volume_name": f"{name}_aiops_data",
                "volume_mountpoint": f"/var/lib/docker/volumes/{name}_aiops_data/_data",
                "filesystem": filesystem,
                "sqlite_present": True,
                "quick_check": "ok",
            },
        }
    return runs, {
        "commit": commit,
        "source_sha": source_sha,
        "registry_image": registry_image,
        "run_id": run_id,
    }


def self_test() -> None:
    baseline, context = synthetic_runs()
    validate_runs(baseline, **context)
    mutations = (
        ("result", "failed"),
        ("artifact_class", "diagnostic"),
        ("commit_sha", "f" * 40),
        ("source_archive_sha256", "0" * 64),
        ("registry_image", "other@sha256:" + "1" * 64),
        ("evidence_name", "wrong"),
        ("os_version", "20.04"),
        ("data_filesystem", "btrfs"),
        ("filesystem", "btrfs"),
        ("root_filesystem", "nfs"),
        ("docker_root_dir", "/tmp/docker"),
        ("docker_storage_driver", ""),
        ("cloud_image_sha256", "bad"),
        ("cloud_image_url", "http://example.invalid/test.img"),
        ("evidence_path", "/tmp/forged.json"),
        ("raw_log_path", "/tmp/forged.log"),
        ("release_digest", "sha256:bad"),
        ("frontend_release_digest", "sha256:bad"),
        ("upstream_backend_image_ref", "ghcr.io/example/backend:latest"),
        ("upstream_frontend_image_ref", "ghcr.io/example/frontend:latest"),
        ("release_manifest_sha256", "sha256:bad"),
        ("release_manifest_path", "/tmp/forged.json"),
        ("release_tag_signature_verified", False),
        ("backend_cosign_verification_sha256", "sha256:bad"),
        ("frontend_cosign_verification_sha256", "sha256:bad"),
        ("backend_cosign_verification_path", "/tmp/forged.json"),
        ("frontend_cosign_verification_path", "/tmp/forged.json"),
        ("backend_provenance_verification_sha256", "sha256:bad"),
        ("frontend_provenance_verification_sha256", "sha256:bad"),
        ("backend_sbom_verification_sha256", "sha256:bad"),
        ("frontend_sbom_verification_sha256", "sha256:bad"),
        ("backend_provenance_verification_path", "/tmp/forged.json"),
        ("frontend_provenance_verification_path", "/tmp/forged.json"),
        ("backend_sbom_verification_path", "/tmp/forged.json"),
        ("frontend_sbom_verification_path", "/tmp/forged.json"),
    )
    target = next(iter(EXPECTED_VARIANTS))
    for field, value in mutations:
        candidate = copy.deepcopy(baseline)
        candidate[target][field] = value
        try:
            validate_runs(candidate, **context)
        except ValidationError:
            continue
        raise SystemExit(f"self-test mutation was accepted: {field}")

    sqlite_mutations = (
        ("sqlite_present", False),
        ("quick_check", "corrupt"),
        ("filesystem", "btrfs"),
        ("volume_name", ""),
        ("volume_mountpoint", "/tmp/forged/_data"),
    )
    for field, value in sqlite_mutations:
        candidate = copy.deepcopy(baseline)
        candidate[target]["sqlite_storage"][field] = value
        try:
            validate_runs(candidate, **context)
        except ValidationError:
            continue
        raise SystemExit(f"self-test SQLite mutation was accepted: {field}")

    candidate = copy.deepcopy(baseline)
    candidate[target]["sqlite_storage"]["volume_name"] = "../../../etc"
    candidate[target]["sqlite_storage"]["volume_mountpoint"] = "/var/lib/docker/volumes/../../../etc/_data"
    try:
        validate_runs(candidate, **context)
    except ValidationError:
        pass
    else:
        raise SystemExit("self-test accepted a path-traversal volume name")

    candidate = copy.deepcopy(baseline)
    candidate[target]["release_digest"] = "sha256:" + "f" * 64
    try:
        validate_runs(candidate, **context)
    except ValidationError:
        pass
    else:
        raise SystemExit("self-test accepted mismatched backend digests")

    for field, value in (
        ("frontend_release_digest", "sha256:" + "f" * 64),
        ("cloud_image_sha256", "3" * 64),
    ):
        candidate = copy.deepcopy(baseline)
        candidate[target][field] = value
        try:
            validate_runs(candidate, **context)
        except ValidationError:
            continue
        raise SystemExit(f"self-test accepted cross-variant mismatch: {field}")

    with tempfile.TemporaryDirectory(prefix="aiops-r0-summary-self-test.") as temporary:
        root = Path(temporary)
        evidence_dir = root / EVIDENCE_PREFIX
        evidence_dir.mkdir(parents=True)
        shared_fixture = b"{}\n"
        shared_fixture_digest = "sha256:" + hashlib.sha256(shared_fixture).hexdigest()
        for run in baseline.values():
            run["release_manifest_sha256"] = shared_fixture_digest
            for prefix in (
                "backend_cosign",
                "frontend_cosign",
                "backend_provenance",
                "frontend_provenance",
                "backend_sbom",
                "frontend_sbom",
            ):
                run[f"{prefix}_verification_sha256"] = shared_fixture_digest
        for name, run in baseline.items():
            (evidence_dir / f"{name}.json").write_text(
                json.dumps(run, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            (evidence_dir / f"{name}-{context['run_id']}.log").write_text("self-test log\n", encoding="utf-8")
        for name in (
            "release-manifest",
            "backend-cosign",
            "frontend-cosign",
            "backend-provenance",
            "frontend-provenance",
            "backend-sbom",
            "frontend-sbom",
        ):
            (evidence_dir / f"{name}-{context['run_id']}.json").write_bytes(shared_fixture)
        payload = build_summary(
            root=root,
            evidence_dir=evidence_dir,
            commit=context["commit"],
            source_sha=context["source_sha"],
            registry_image=context["registry_image"],
            run_id=context["run_id"],
        )
        require(payload["result"] == "passed", "full-path self-test did not produce a passed payload")
        require(len(payload["evidence_sha256"]) == 15, "full-path self-test did not hash all artifacts")
        publish_summary(evidence_dir, payload, run_id=context["run_id"], commit=context["commit"])
        require((evidence_dir / "matrix-summary.json").is_file(), "summary was not published")
        require(json.loads((evidence_dir / "matrix-status.json").read_text())["result"] == "passed", "passed status was not published")
        invalidate_summary(evidence_dir, context["run_id"])
        require(not (evidence_dir / "matrix-summary.json").exists(), "invalidation left the prior summary")
        require(json.loads((evidence_dir / "matrix-status.json").read_text())["result"] == "in_progress", "in-progress status was not published")
        stage_summary(evidence_dir, payload, run_id=context["run_id"], commit=context["commit"])
        require((evidence_dir / f".matrix-summary.{context['run_id']}.pending").is_file(), "summary was not staged")
        require((evidence_dir / f".matrix-status.{context['run_id']}.pending").is_file(), "status was not staged")

        log_path = evidence_dir / f"{target}-{context['run_id']}.log"
        outside = root / "outside.log"
        outside.write_text("outside\n", encoding="utf-8")
        log_path.unlink()
        log_path.symlink_to(outside)
        try:
            build_summary(
                root=root,
                evidence_dir=evidence_dir,
                commit=context["commit"],
                source_sha=context["source_sha"],
                registry_image=context["registry_image"],
                run_id=context["run_id"],
            )
        except ValidationError:
            pass
        else:
            raise SystemExit("self-test accepted a symlinked evidence artifact")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--invalidate", action="store_true")
    parser.add_argument("--stage", action="store_true")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--commit")
    parser.add_argument("--source-archive-sha256")
    parser.add_argument("--registry-image")
    parser.add_argument("--run-id")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        print("R0 matrix summary adversarial self-test passed")
        return 0
    if args.invalidate:
        if args.root is None or args.evidence_dir is None or args.run_id is None:
            raise SystemExit("--invalidate requires --root, --evidence-dir and --run-id")
        root = args.root.resolve()
        evidence_dir = args.evidence_dir.absolute()
        require(evidence_dir == root / EVIDENCE_PREFIX, "evidence directory is outside the fixed repository path")
        require_no_symlink_components(root, Path(EVIDENCE_PREFIX))
        invalidate_summary(evidence_dir, args.run_id)
        return 0
    required = {
        "root": args.root,
        "evidence_dir": args.evidence_dir,
        "commit": args.commit,
        "source_archive_sha256": args.source_archive_sha256,
        "registry_image": args.registry_image,
        "run_id": args.run_id,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise SystemExit(f"missing required arguments: {', '.join(missing)}")
    root = args.root.resolve()
    evidence_dir = args.evidence_dir.absolute()
    payload = build_summary(
        root=root,
        evidence_dir=evidence_dir,
        commit=args.commit,
        source_sha=args.source_archive_sha256,
        registry_image=args.registry_image,
        run_id=args.run_id,
    )
    if args.stage:
        stage_summary(evidence_dir, payload, run_id=args.run_id, commit=args.commit)
    else:
        publish_summary(evidence_dir, payload, run_id=args.run_id, commit=args.commit)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, ValidationError) as error:
        raise SystemExit(f"R0 matrix summary validation failed: {error}") from error
