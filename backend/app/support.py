from __future__ import annotations

import platform
from pathlib import Path
from typing import Mapping


SUPPORTED_UBUNTU_VERSIONS = {"22.04", "24.04"}
SUPPORTED_ARCHITECTURES = {"x86_64", "amd64"}


def read_os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"')
    except OSError:
        return {}
    return values


def evaluate_support(
    *,
    system: str | None = None,
    machine: str | None = None,
    os_release: Mapping[str, str] | None = None,
) -> dict:
    detected_system = system or platform.system()
    detected_machine = (machine or platform.machine()).lower()
    release = dict(read_os_release() if os_release is None else os_release)
    distro = release.get("ID", "unknown").lower()
    version = release.get("VERSION_ID", "unknown")
    supported = (
        detected_system == "Linux"
        and distro == "ubuntu"
        and version in SUPPORTED_UBUNTU_VERSIONS
        and detected_machine in SUPPORTED_ARCHITECTURES
    )
    reason_codes: list[str] = []
    if detected_system != "Linux" or distro != "ubuntu":
        reason_codes.append("unsupported_operating_system")
    if version not in SUPPORTED_UBUNTU_VERSIONS:
        reason_codes.append("unsupported_ubuntu_version")
    if detected_machine not in SUPPORTED_ARCHITECTURES:
        reason_codes.append("unsupported_architecture")
    return {
        "status": "supported" if supported else "unsupported_for_mutation",
        "mutation_supported": supported,
        "detected": {
            "system": detected_system,
            "distribution": distro,
            "version": version,
            "architecture": detected_machine,
        },
        "supported": {
            "distribution": "ubuntu",
            "versions": sorted(SUPPORTED_UBUNTU_VERSIONS),
            "architectures": sorted(SUPPORTED_ARCHITECTURES),
        },
        "reason_codes": reason_codes,
    }
