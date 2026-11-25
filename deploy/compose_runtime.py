"""Root/operator helper: reuse existing container environments without .env files.

For upgrades of this deployment only. Initial provisioning is performed separately
with secrets supplied through the caller environment. Never exposes an HTTP API.
"""
import json
import os
import subprocess
import sys
from pathlib import Path


def container_env(name):
    item = json.loads(subprocess.check_output(["docker", "inspect", name], text=True))[0]
    return dict(v.split("=", 1) for v in item["Config"]["Env"] if "=" in v)


def _host_mem_mib() -> int:
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return 0
    for line in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1]) // 1024
    return 0


def main():
    args = sys.argv[1:]
    root = Path(__file__).resolve().parent
    release_file = root / "release.json"
    release = json.loads(release_file.read_text()) if release_file.exists() else {}
    tls = "--tls" in args or release.get("tls_enabled", False)
    args = [arg for arg in args if arg != "--tls"]
    if not args or args[0] not in {"up", "stop", "start", "ps", "restart", "logs", "config"}:
        raise SystemExit("Allowed: [--tls] up|stop|start|ps|restart|logs|config --quiet")
    if args[0] == "config" and args != ["config", "--quiet"]:
        raise SystemExit("Only config --quiet is allowed: rendered config contains secrets")
    db = container_env("cyshuhe-db-1")
    migration = container_env("cyshuhe-migrate-1")
    api = container_env("cyshuhe-api-1")
    caller_env = dict(os.environ)
    env = dict(caller_env)
    env.update({key: value for key, value in api.items() if key.startswith("CYSH_")})
    host_mib = _host_mem_mib()
    if 0 < host_mib <= 2300:
        low_ram_defaults = {
            "CYSH_DB_MEM_LIMIT": "544m",
            "CYSH_PG_SHARED_BUFFERS": "96MB",
            "CYSH_PG_WORK_MEM": "2MB",
            "CYSH_PG_MAX_CONNECTIONS": "40",
            "CYSH_API_MEM_LIMIT": "544m",
            "CYSH_DB_POOL_SIZE": "6",
            "CYSH_DB_MAX_OVERFLOW": "6",
            "CYSH_WORKER_MEM_LIMIT": "224m",
            "CYSH_WORKER_POOL_SIZE": "1",
            "CYSH_WORKER_MAX_OVERFLOW": "1",
        }
        for k, v in low_ram_defaults.items():
            env[k] = caller_env.get(k, v)
    env.update(
        POSTGRES_PASSWORD=db["POSTGRES_PASSWORD"],
        CYSH_RUNTIME_DB_PASSWORD=migration["CYSH_RUNTIME_DB_PASSWORD"],
        CYSH_JWT_SECRET=migration["CYSH_JWT_SECRET"],
        CYSH_RELEASE=caller_env.get("CYSH_RELEASE", release.get("release_tag", "s12-stage")),
    )
    command = ["docker", "compose", "-f", str(root / "docker-compose.yml")]
    if tls:
        command += ["-f", str(root / "docker-compose.tls.yml")]
    # No shell and no credential-bearing command arguments.
    raise SystemExit(subprocess.call(command + args, env=env))


if __name__ == "__main__":
    main()
