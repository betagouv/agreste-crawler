"""Fetch Scalingo env (DB + S3), rewrite DATABASE_URL for a local SSH tunnel."""

from __future__ import annotations

import argparse
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

REQUIRED_S3_ENV_KEYS = (
    "S3_BUCKET_NAME",
    "S3_BUCKET_REGION",
    "S3_HOST",
    "S3_KEY_ID",
    "S3_KEY_SECRET",
    "S3_LOCATION",
)

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


def add_scalingo_app_args(parser: argparse.ArgumentParser) -> None:
    """Register ``--scalingo-app`` and ``--database-port`` on ``parser``."""
    parser.add_argument(
        "--scalingo-app",
        type=str,
        default="",
        help=(
            "Scalingo app name: fetch SCALINGO_POSTGRESQL_URL, set "
            "DATABASE_URL to 127.0.0.1:<database-port>, and open an SSH tunnel."
        ),
    )
    parser.add_argument(
        "--database-port",
        type=int,
        default=DEFAULT_DATABASE_PORT,
        help=(
            "Local SSH tunnel port used in DATABASE_URL "
            f"(default: {DEFAULT_DATABASE_PORT})."
        ),
    )


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


def _fetch_scalingo_env(app: str) -> dict[str, str]:
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

    env: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key] = value
    return env


def _require_env_value(env: dict[str, str], key: str, app: str) -> str:
    if key not in env:
        raise RuntimeError(
            f"{key} not found in scalingo --app {app} env output."
        )
    return env[key]


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
    If ``--scalingo-app`` is set, fetch Scalingo env once and set:

    - ``DATABASE_URL`` rewritten to ``127.0.0.1:<--database-port>``
      (default 10001)
    - S3 vars: ``S3_BUCKET_NAME``, ``S3_BUCKET_REGION``, ``S3_HOST``,
      ``S3_KEY_ID``, ``S3_KEY_SECRET``, ``S3_LOCATION``

    No-op when ``--scalingo-app`` is absent. Safe to call before
    ``setup_django`` (survives venv re-exec via environ).
    """
    app = _get_scalingo_app_arg()
    if not app:
        return

    local_port = _get_database_port_arg()
    scalingo_env = _fetch_scalingo_env(app)
    remote_url = _require_env_value(scalingo_env, "SCALINGO_POSTGRESQL_URL", app)
    remote_host, remote_port = _parse_postgres_url(remote_url)
    tunnel_url = _rewrite_url_for_tunnel(remote_url, local_port)

    os.environ["DATABASE_URL"] = tunnel_url
    os.environ[_ENV_APP] = app
    os.environ[_ENV_LOCAL_PORT] = str(local_port)
    os.environ[_ENV_REMOTE_HOST] = remote_host
    os.environ[_ENV_REMOTE_PORT] = str(remote_port)

    for key in REQUIRED_S3_ENV_KEYS:
        os.environ[key] = _require_env_value(scalingo_env, key, app)

    print(
        f"Configured database connection via Scalingo app {app!r}.",
        flush=True,
    )
    print(
        f"Loaded S3 env vars from Scalingo app {app!r}: "
        f"{', '.join(REQUIRED_S3_ENV_KEYS)}.",
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


def _port_is_open(port: int, timeout_s: float = 0.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _raise_ssh_failed(app: str, proc: subprocess.Popen[bytes]) -> None:
    # Do not include ssh stderr: it often contains host/port details.
    raise RuntimeError(
        f"SSH tunnel to Scalingo app {app!r} failed "
        f"(code {proc.returncode})."
    )


def _wait_for_tunnel_port(
    port: int,
    proc: subprocess.Popen[bytes],
    app: str,
    timeout_s: float = 15.0,
) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            _raise_ssh_failed(app, proc)
        if _port_is_open(port):
            if proc.poll() is not None:
                _raise_ssh_failed(app, proc)
            return
        time.sleep(0.2)
    if proc.poll() is not None:
        _raise_ssh_failed(app, proc)
    raise RuntimeError(
        f"SSH tunnel did not open within {timeout_s:.0f}s."
    )


def _ensure_local_port_free(port: int) -> None:
    if _port_is_open(port):
        raise RuntimeError(
            "Local database tunnel port is already in use. "
            "Free it or pass a different --database-port."
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

    _ensure_local_port_free(local_port)

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
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "ssh not found on PATH. Install an OpenSSH client to use "
            "--scalingo-app."
        ) from exc

    try:
        _wait_for_tunnel_port(local_port, _ssh_process, app)
    except Exception:
        _stop_ssh_tunnel()
        raise

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

    print(
        f"SSH tunnel ready for Scalingo app {app!r}.",
        flush=True,
    )
