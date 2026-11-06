"""Fetch Scalingo Postgres URL, rewrite for a local SSH tunnel, open the tunnel."""

from __future__ import annotations

import atexit
import os
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse, urlunparse

DEFAULT_DATABASE_PORT = 10001
SCALINGO_SSH_HOST = "ssh.osc-fr1.scalingo.com"
SCALINGO_SSH_PORT = "22"

# Survive django_setup re-exec (os.execve copies os.environ).
_ENV_APP = "SCALINGO_DB_TUNNEL_APP"
_ENV_LOCAL_PORT = "SCALINGO_DB_TUNNEL_LOCAL_PORT"
_ENV_REMOTE_HOST = "SCALINGO_DB_TUNNEL_REMOTE_HOST"
_ENV_REMOTE_PORT = "SCALINGO_DB_TUNNEL_REMOTE_PORT"

_ssh_process: subprocess.Popen[bytes] | None = None
_tunnel_started = False


def _get_cli_arg(name: str) -> str | None:
    flag = f"--{name}"
    for i, arg in enumerate(sys.argv):
        if arg == flag:
            if i + 1 >= len(sys.argv):
                raise ValueError(f"{flag} requires an argument.")
            return sys.argv[i + 1]
        if arg.startswith(f"{flag}="):
            value = arg.split("=", 1)[1]
            if not value:
                raise ValueError(f"{flag} requires a non-empty argument.")
            return value
    return None


def _get_scalingo_app_arg() -> str | None:
    return _get_cli_arg("scalingo-app")


def _get_database_port_arg() -> int:
    raw = _get_cli_arg("database-port")
    if raw is None:
        return DEFAULT_DATABASE_PORT
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"--database-port must be an integer, got {raw!r}."
        ) from exc
    if not (1 <= port <= 65535):
        raise ValueError(f"--database-port out of range: {port}")
    return port


def _fetch_scalingo_postgresql_url(app: str) -> str:
    try:
        result = subprocess.run(
            ["scalingo", "--app", app, "env"],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "scalingo CLI not found on PATH. Install it to use --scalingo-app."
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise RuntimeError(
            f"scalingo --app {app} env failed"
            + (f": {stderr}" if stderr else ".")
        ) from exc

    for line in result.stdout.splitlines():
        if line.startswith("SCALINGO_POSTGRESQL_URL="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError(
        f"SCALINGO_POSTGRESQL_URL not found in scalingo --app {app} env output."
    )


def _parse_postgres_url(url: str) -> tuple[str, int]:
    """Return ``(remote_host, remote_port)`` from a Postgres URL."""
    parsed = urlparse(url)
    if not parsed.hostname:
        raise ValueError("SCALINGO_POSTGRESQL_URL has no hostname.")
    if parsed.port is None:
        raise ValueError("SCALINGO_POSTGRESQL_URL has no port.")
    return parsed.hostname, parsed.port


def _rewrite_url_for_tunnel(url: str, local_port: int) -> str:
    parsed = urlparse(url)
    # netloc is user:password@host:port — rebuild with local host/port.
    userinfo = ""
    if parsed.username is not None:
        userinfo = parsed.username
        if parsed.password is not None:
            userinfo = f"{userinfo}:{parsed.password}"
        userinfo = f"{userinfo}@"
    netloc = f"{userinfo}127.0.0.1:{local_port}"
    return urlunparse(
        (
            parsed.scheme,
            netloc,
            parsed.path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )


def configure_database_url() -> None:
    """
    If ``--scalingo-app`` is set, fetch Postgres URL and set ``DATABASE_URL``
    to ``127.0.0.1:<--database-port>`` (default 10001).

    No-op when ``--scalingo-app`` is absent. Safe to call before
    ``setup_django`` (survives venv re-exec via environ).
    """
    app = _get_scalingo_app_arg()
    if not app:
        return

    local_port = _get_database_port_arg()
    remote_url = _fetch_scalingo_postgresql_url(app)
    remote_host, remote_port = _parse_postgres_url(remote_url)
    tunnel_url = _rewrite_url_for_tunnel(remote_url, local_port)

    os.environ["DATABASE_URL"] = tunnel_url
    os.environ[_ENV_APP] = app
    os.environ[_ENV_LOCAL_PORT] = str(local_port)
    os.environ[_ENV_REMOTE_HOST] = remote_host
    os.environ[_ENV_REMOTE_PORT] = str(remote_port)

    print(
        f"Configured DATABASE_URL via Scalingo app {app!r} "
        f"(local tunnel port {local_port}).",
        flush=True,
    )


def _stop_ssh_tunnel() -> None:
    global _ssh_process
    proc = _ssh_process
    if proc is None:
        return
    _ssh_process = None
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _wait_for_local_port(port: int, timeout_s: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(
        f"SSH tunnel did not open 127.0.0.1:{port} within {timeout_s:.0f}s."
    )


def ensure_ssh_tunnel() -> None:
    """
    Open ``ssh -L`` to Scalingo when ``configure_database_url`` ran.

    Call after ``setup_django`` so a venv re-exec does not orphan the tunnel.
    Closes the tunnel on process exit via ``atexit``.
    """
    global _ssh_process, _tunnel_started

    app = os.environ.get(_ENV_APP)
    if not app:
        return
    if _tunnel_started:
        return

    local_port = int(os.environ[_ENV_LOCAL_PORT])
    remote_host = os.environ[_ENV_REMOTE_HOST]
    remote_port = os.environ[_ENV_REMOTE_PORT]
    forward = f"{local_port}:{remote_host}:{remote_port}"

    try:
        _ssh_process = subprocess.Popen(
            [
                "ssh",
                "-L",
                forward,
                f"git@{SCALINGO_SSH_HOST}",
                "-p",
                SCALINGO_SSH_PORT,
                "-N",
                "-o",
                "ExitOnForwardFailure=yes",
                "-o",
                "ServerAliveInterval=30",
            ],
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "ssh not found on PATH. Install an OpenSSH client to use "
            "--scalingo-app."
        ) from exc

    _tunnel_started = True
    atexit.register(_stop_ssh_tunnel)

    def _on_signal(signum: int, _frame: object) -> None:
        _stop_ssh_tunnel()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            pass

    if _ssh_process.poll() is not None:
        raise RuntimeError(
            f"SSH tunnel to Scalingo app {app!r} exited immediately "
            f"(code {_ssh_process.returncode})."
        )

    _wait_for_local_port(local_port)
    print(
        f"SSH tunnel ready: 127.0.0.1:{local_port} -> "
        f"{remote_host}:{remote_port} (app {app!r}).",
        flush=True,
    )
