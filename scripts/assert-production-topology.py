#!/usr/bin/env python3
"""Fail-closed assertions for the R0 production Compose topology."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


EXPECTED_USERS = {"backend": "10001:10001", "frontend": "101:101"}
EXPECTED_SECRET_TARGETS = {
    "backend": {
        "/run/secrets/session_secret",
        "/run/secrets/llm_api_key",
        "/run/secrets/llm_evidence",
    },
    "frontend": {"/run/secrets/tls_cert", "/run/secrets/tls_key"},
}
FORBIDDEN_SOCKET_PATHS = {
    "/var/run/docker.sock",
    "/run/docker.sock",
    "/run/aiops/agent.sock",
    "/tmp/aiops-agent.sock",
}
IMMUTABLE_IMAGE_PATTERN = re.compile(
    r"^(?:sha256:[0-9a-f]{64}|[^@\s]+@sha256:[0-9a-f]{64})$"
)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def assert_no_forbidden_path(path_value: str, *, label: str) -> None:
    normalized = str(Path(path_value))
    require(normalized != "/", f"{label} must not mount host/container root")
    require(normalized not in FORBIDDEN_SOCKET_PATHS, f"{label} exposes forbidden socket {normalized}")
    require(not normalized.startswith("/run/aiops/"), f"{label} exposes Agent runtime path")
    require(not normalized.endswith("/docker.sock"), f"{label} exposes Docker socket")


def assert_service_security(service_name: str, service: dict[str, Any]) -> None:
    require(service.get("user") == EXPECTED_USERS[service_name], f"{service_name} user drifted")
    require(service.get("read_only") is True, f"{service_name} root filesystem must be read-only")
    require(not service.get("privileged", False), f"{service_name} must not be privileged")
    require(not service.get("cap_add"), f"{service_name} CapAdd must be empty")
    require(service.get("cap_drop") == ["ALL"], f"{service_name} must drop ALL capabilities")
    require(
        set(service.get("security_opt") or []) == {"no-new-privileges:true"},
        f"{service_name} security_opt must contain only no-new-privileges:true",
    )
    require(service.get("pid") != "host", f"{service_name} must not use host PID namespace")
    require(service.get("ipc") != "host", f"{service_name} must not use host IPC namespace")
    require(service.get("network_mode") != "host", f"{service_name} must not use host network")
    require(not service.get("devices"), f"{service_name} must not expose host devices")


def image_digest(image_ref: str) -> str:
    require(
        bool(IMMUTABLE_IMAGE_PATTERN.fullmatch(image_ref)),
        f"image reference is not immutable: {image_ref}",
    )
    return image_ref if image_ref.startswith("sha256:") else image_ref.rsplit("@", 1)[1]


def assert_compose(payload: dict[str, Any], *, setup: bool, https_port: str) -> None:
    services = payload.get("services") or {}
    require(set(services) == {"backend", "frontend"}, "production Compose service allowlist drifted")

    for service_name, service in services.items():
        assert_service_security(service_name, service)

    backend = services["backend"]
    frontend = services["frontend"]
    backend_digest = image_digest(str(backend.get("image") or ""))
    image_digest(str(frontend.get("image") or ""))
    backend_environment = backend.get("environment") or {}
    require(
        backend_environment.get("AIOPS_RELEASE_DIGEST") == backend_digest,
        "backend release digest must match its immutable image reference",
    )
    require(not backend.get("ports"), "backend must not publish host ports")
    require(
        backend.get("volumes") == [
            {"type": "volume", "source": "aiops_data", "target": "/app/data", "volume": {}}
        ],
        "backend volume allowlist drifted",
    )
    require(not frontend.get("volumes"), "production frontend config must be embedded in the image")

    backend_secret_targets = {item["target"] for item in backend.get("secrets") or []}
    expected_backend_secrets = set(EXPECTED_SECRET_TARGETS["backend"])
    if setup:
        expected_backend_secrets.add("/run/secrets/setup_token")
    require(backend_secret_targets == expected_backend_secrets, "backend secret allowlist drifted")
    frontend_secret_targets = {item["target"] for item in frontend.get("secrets") or []}
    require(frontend_secret_targets == EXPECTED_SECRET_TARGETS["frontend"], "frontend secret allowlist drifted")

    frontend_ports = frontend.get("ports") or []
    require(len(frontend_ports) == 1, "frontend must publish exactly one HTTPS port")
    published = frontend_ports[0]
    require(published.get("target") == 8443, "frontend container port must be 8443")
    require(str(published.get("published")) == https_port, "frontend published port drifted")
    require(published.get("host_ip") == "127.0.0.1", "test topology must bind HTTPS to loopback")
    require(published.get("protocol") == "tcp", "frontend HTTPS must use TCP")

    for service_name, service in services.items():
        for mount in service.get("volumes") or []:
            assert_no_forbidden_path(mount.get("source", ""), label=f"{service_name} volume source")
            assert_no_forbidden_path(mount.get("target", ""), label=f"{service_name} volume target")
        for secret in service.get("secrets") or []:
            assert_no_forbidden_path(secret.get("target", ""), label=f"{service_name} secret target")

    for secret_name, secret in (payload.get("secrets") or {}).items():
        assert_no_forbidden_path(secret.get("file", ""), label=f"secret {secret_name} source")


def assert_runtime(payload: list[dict[str, Any]], *, https_port: str) -> None:
    by_service: dict[str, dict[str, Any]] = {}
    for container in payload:
        labels = (container.get("Config") or {}).get("Labels") or {}
        service_name = labels.get("com.docker.compose.service")
        require(service_name in EXPECTED_USERS, f"unexpected runtime service {service_name!r}")
        require(service_name not in by_service, f"duplicate runtime service {service_name}")
        by_service[service_name] = container
    require(set(by_service) == {"backend", "frontend"}, "runtime service allowlist drifted")

    expected_mounts = {
        "backend": {
            "/app/data": ("volume", True),
            "/run/secrets/session_secret": ("bind", False),
            "/run/secrets/llm_api_key": ("bind", False),
            "/run/secrets/llm_evidence": ("bind", False),
        },
        "frontend": {
            "/run/secrets/tls_cert": ("bind", False),
            "/run/secrets/tls_key": ("bind", False),
        },
    }

    for service_name, container in by_service.items():
        config = container.get("Config") or {}
        host = container.get("HostConfig") or {}
        configured_image = str(config.get("Image") or "")
        configured_digest = image_digest(configured_image)
        if configured_image.startswith("sha256:"):
            require(
                container.get("Image") == configured_image,
                f"{service_name} runtime image ID drifted",
            )
        require(config.get("User") == EXPECTED_USERS[service_name], f"{service_name} runtime user drifted")
        require(host.get("ReadonlyRootfs") is True, f"{service_name} runtime rootfs must be read-only")
        require(host.get("Privileged") is False, f"{service_name} runtime must not be privileged")
        require(not host.get("CapAdd"), f"{service_name} runtime CapAdd must be empty")
        require(host.get("CapDrop") == ["ALL"], f"{service_name} runtime must drop ALL capabilities")
        require(
            set(host.get("SecurityOpt") or []) == {"no-new-privileges:true"},
            f"{service_name} runtime security options drifted",
        )
        require(host.get("PidMode") != "host", f"{service_name} runtime uses host PID namespace")
        require(host.get("IpcMode") != "host", f"{service_name} runtime uses host IPC namespace")
        require(host.get("NetworkMode") != "host", f"{service_name} runtime uses host network")
        require(not host.get("Devices"), f"{service_name} runtime exposes host devices")
        require(not host.get("DeviceRequests"), f"{service_name} runtime requests devices")

        env_names = {item.split("=", 1)[0] for item in config.get("Env") or []}
        require("AIOPS_SESSION_SECRET" not in env_names, "direct session secret leaked into environment")
        require("AIOPS_LLM_API_KEY" not in env_names, "direct LLM secret leaked into environment")
        require(
            "AIOPS_BOOTSTRAP_ADMIN_PASSWORD" not in env_names,
            "bootstrap password leaked into production environment",
        )
        if service_name == "backend":
            environment = dict(item.split("=", 1) for item in config.get("Env") or [] if "=" in item)
            require(
                environment.get("AIOPS_RELEASE_DIGEST") == configured_digest,
                "backend runtime release digest does not match configured image",
            )

        actual_mounts: dict[str, tuple[str, bool]] = {}
        for mount in container.get("Mounts") or []:
            source = mount.get("Source", "")
            destination = mount.get("Destination", "")
            assert_no_forbidden_path(source, label=f"{service_name} runtime mount source")
            assert_no_forbidden_path(destination, label=f"{service_name} runtime mount target")
            require(destination not in actual_mounts, f"duplicate mount target {destination}")
            actual_mounts[destination] = (mount.get("Type"), bool(mount.get("RW")))
        require(actual_mounts == expected_mounts[service_name], f"{service_name} runtime mount allowlist drifted: {actual_mounts}")

    backend_bindings = (by_service["backend"].get("HostConfig") or {}).get("PortBindings")
    require(not backend_bindings, "backend runtime must not have PortBindings")
    frontend_bindings = (by_service["frontend"].get("HostConfig") or {}).get("PortBindings") or {}
    require(set(frontend_bindings) == {"8443/tcp"}, "frontend runtime port allowlist drifted")
    binding_rows = frontend_bindings["8443/tcp"] or []
    require(len(binding_rows) == 1, "frontend must have one HTTPS binding")
    require(binding_rows[0].get("HostIp") == "127.0.0.1", "runtime HTTPS must bind loopback")
    require(binding_rows[0].get("HostPort") == https_port, "runtime HTTPS host port drifted")


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    compose_parser = subparsers.add_parser("compose")
    compose_parser.add_argument("json_path", type=Path)
    compose_parser.add_argument("--setup", action="store_true")
    compose_parser.add_argument("--https-port", required=True)

    runtime_parser = subparsers.add_parser("runtime")
    runtime_parser.add_argument("json_path", type=Path)
    runtime_parser.add_argument("--https-port", required=True)

    args = parser.parse_args()
    if args.command == "compose":
        assert_compose(load_json(args.json_path), setup=args.setup, https_port=args.https_port)
    else:
        assert_runtime(load_json(args.json_path), https_port=args.https_port)
    print(f"production topology {args.command} assertions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
