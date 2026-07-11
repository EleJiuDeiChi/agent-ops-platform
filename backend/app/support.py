from __future__ import annotations

import platform
import re
from pathlib import Path
from typing import Mapping


SUPPORTED_UBUNTU_VERSIONS = {"22.04", "24.04"}
SUPPORTED_ARCHITECTURES = {"x86_64", "amd64"}
SUPPORTED_DATA_FILESYSTEMS = {"ext4", "xfs"}
NETWORK_DATA_FILESYSTEMS = {
    "9p",
    "ceph",
    "cifs",
    "fuse.sshfs",
    "glusterfs",
    "nfs",
    "nfs4",
    "smb",
    "smb2",
    "smb3",
}
MOUNTINFO_PATH = Path("/proc/self/mountinfo")


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


def read_mountinfo(path: Path = MOUNTINFO_PATH) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _decode_mountinfo_path(value: str) -> str:
    """Decode the octal escapes used for whitespace and backslashes in mountinfo."""

    return re.sub(
        r"\\([0-7]{3})",
        lambda match: chr(int(match.group(1), 8)),
        value,
    )


def filesystem_for_path(
    path: Path | str,
    *,
    mountinfo_text: str | None = None,
) -> str | None:
    """Return the filesystem of the most specific mount containing ``path``.

    ``mountinfo_text`` is injectable so the production boundary can be tested
    without depending on the developer or CI host filesystem. Passing ``None``
    reads the current process mount namespace from ``/proc/self/mountinfo``.
    """

    source = read_mountinfo() if mountinfo_text is None else mountinfo_text
    if not source:
        return None

    try:
        target = Path(path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return None

    best_depth = -1
    best_filesystem: str | None = None
    for line in source.splitlines():
        fields = line.split()
        try:
            separator = fields.index("-")
        except ValueError:
            continue
        if separator < 6 or separator + 3 >= len(fields):
            continue

        mountpoint = Path(_decode_mountinfo_path(fields[4]))
        if not mountpoint.is_absolute():
            continue
        if target != mountpoint and mountpoint not in target.parents:
            continue

        depth = len(mountpoint.parts)
        if depth >= best_depth:
            best_depth = depth
            best_filesystem = fields[separator + 1].lower()

    return best_filesystem


def evaluate_support(
    *,
    system: str | None = None,
    machine: str | None = None,
    os_release: Mapping[str, str] | None = None,
    database_path: Path | str | None = None,
    mountinfo_text: str | None = None,
) -> dict:
    detected_system = system or platform.system()
    detected_machine = (machine or platform.machine()).lower()
    release = dict(read_os_release() if os_release is None else os_release)
    distro = release.get("ID", "unknown").lower()
    version = release.get("VERSION_ID", "unknown")
    platform_supported = (
        detected_system == "Linux"
        and distro == "ubuntu"
        and version in SUPPORTED_UBUNTU_VERSIONS
        and detected_machine in SUPPORTED_ARCHITECTURES
    )
    data_filesystem = (
        filesystem_for_path(database_path, mountinfo_text=mountinfo_text)
        if database_path is not None
        else None
    )
    filesystem_supported = (
        data_filesystem in SUPPORTED_DATA_FILESYSTEMS
        if database_path is not None
        else True
    )
    supported = platform_supported and filesystem_supported
    reason_codes: list[str] = []
    if detected_system != "Linux" or distro != "ubuntu":
        reason_codes.append("unsupported_operating_system")
    if version not in SUPPORTED_UBUNTU_VERSIONS:
        reason_codes.append("unsupported_ubuntu_version")
    if detected_machine not in SUPPORTED_ARCHITECTURES:
        reason_codes.append("unsupported_architecture")
    if database_path is not None and data_filesystem is None:
        reason_codes.extend(
            ["data_filesystem_unknown", "unsupported_data_filesystem"]
        )
    elif data_filesystem in NETWORK_DATA_FILESYSTEMS:
        reason_codes.extend(
            ["network_data_filesystem", "unsupported_data_filesystem"]
        )
    elif database_path is not None and not filesystem_supported:
        reason_codes.append("unsupported_data_filesystem")
    return {
        "status": "supported" if supported else "unsupported_for_mutation",
        "mutation_supported": supported,
        "detected": {
            "system": detected_system,
            "distribution": distro,
            "version": version,
            "architecture": detected_machine,
            "data_filesystem": data_filesystem or "unknown",
        },
        "supported": {
            "distribution": "ubuntu",
            "versions": sorted(SUPPORTED_UBUNTU_VERSIONS),
            "architectures": sorted(SUPPORTED_ARCHITECTURES),
            "data_filesystems": sorted(SUPPORTED_DATA_FILESYSTEMS),
        },
        "reason_codes": reason_codes,
    }
