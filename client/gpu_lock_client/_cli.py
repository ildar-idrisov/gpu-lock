"""Command-line interface for gpu-lock — `gpu-lock` entry point.

Replaces the old `gpu-run` bash wrapper. Same semantics:
    gpu-lock run --gpu 0 -- python my_script.py
    gpu-lock run --gpu auto --priority high -- bash
    gpu-lock status
    gpu-lock status --gpu 0
    gpu-lock queue
    gpu-lock acquire --gpu 0 --owner me
    gpu-lock release <lease-id>
    gpu-lock renew <lease-id> --ttl 600

`run` acquires a lease, exports CUDA_VISIBLE_DEVICES, runs the given command
while heart-beating in a background thread, then releases on exit.

If `GPU_LOCK_URL` is empty, `run` executes the command directly (passthrough).
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
from typing import Optional

import click

from . import __version__
from ._client import (
    _enabled,
    _headers,
    _url,
    acquire_sync,
    release_sync,
    renew_sync,
)
from ._types import Priority, PriorityLike


@click.group(help="gpu-lock client — acquire/release/inspect GPU leases.")
@click.version_option(__version__, "-V", "--version")
def cli() -> None:
    pass


def _http_get(path: str, **params) -> Optional[dict]:
    if not _enabled():
        click.echo("GPU_LOCK_URL is not set — nothing to query.", err=True)
        raise SystemExit(2)
    import httpx
    with httpx.Client(timeout=10, headers=_headers()) as client:
        r = client.get(f"{_url()}{path}", params={k: v for k, v in params.items() if v is not None})
        r.raise_for_status()
        return r.json()


def _http_post(path: str, **params) -> Optional[dict]:
    if not _enabled():
        click.echo("GPU_LOCK_URL is not set — nothing to do.", err=True)
        raise SystemExit(2)
    import httpx
    with httpx.Client(timeout=30, headers=_headers()) as client:
        r = client.post(f"{_url()}{path}", params={k: v for k, v in params.items() if v is not None})
        r.raise_for_status()
        return r.json() if r.content else None


# ---------------------------------------------------------------------------
# run — the main workflow. Wraps any shell command.
# ---------------------------------------------------------------------------

@cli.command(help="Acquire a lease, export CUDA_VISIBLE_DEVICES, run a command, release.")
@click.option("--gpu", required=False, envvar="GPU_LOCK_GPU",
              help="GPU id (e.g. '0') or 'auto'. Required unless GPU_LOCK_URL is empty.")
@click.option("--owner", default=None, envvar="GPU_LOCK_OWNER",
              help="Owner label (default: hostname).")
@click.option("--ttl", type=float, default=600, envvar="GPU_LOCK_TTL",
              help="Lease TTL in seconds (renewed automatically).")
@click.option("--wait-timeout", type=float, default=600, envvar="GPU_LOCK_WAIT_TIMEOUT",
              help="Max seconds to wait in queue before failing.")
@click.option("--priority",
              type=click.Choice([p.value for p in Priority], case_sensitive=False),
              default=Priority.NORMAL.value, envvar="GPU_LOCK_PRIORITY")
@click.option("--no-heartbeat", is_flag=True, default=False,
              help="Disable background lease renewal (useful for short commands).")
@click.argument("command", nargs=-1, required=True, type=click.UNPROCESSED)
def run(
    gpu: Optional[str],
    owner: Optional[str],
    ttl: float,
    wait_timeout: float,
    priority: str,
    no_heartbeat: bool,
    command: tuple[str, ...],
) -> None:
    cmd = list(command)
    if cmd and cmd[0] == "--":  # allow `gpu-lock run --gpu 0 -- python x.py`
        cmd = cmd[1:]
    if not cmd:
        click.echo("no command given", err=True)
        raise SystemExit(2)

    # Passthrough: no URL → just exec the command.
    if not _enabled():
        os.execvp(cmd[0], cmd)
        return

    if gpu is None or gpu == "":
        click.echo("--gpu is required (e.g. '0' or 'auto'), or set GPU_LOCK_GPU.", err=True)
        raise SystemExit(2)

    owner = owner or socket.gethostname()
    lease = acquire_sync(owner, gpu, ttl=ttl, wait_timeout=wait_timeout, priority=priority)
    if lease is None:
        click.echo("[gpu-lock] lock service unreachable — proceeding without lock", err=True)
        os.execvp(cmd[0], cmd)
        return

    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = str(lease.gpu)
    click.echo(
        f"[gpu-lock] lease={lease.lease_id} owner={owner} gpu={lease.gpu} "
        f"priority={lease.priority.value} ttl={lease.ttl}s "
        f"CUDA_VISIBLE_DEVICES={lease.gpu}",
        err=True,
    )

    # Start heartbeat.
    stop_flag = threading.Event()

    def _heartbeat() -> None:
        from ._client import _heartbeat_interval
        interval = _heartbeat_interval(lease.ttl)
        while not stop_flag.wait(interval):
            renew_sync(lease)

    hb_thread: Optional[threading.Thread] = None
    if not no_heartbeat:
        hb_thread = threading.Thread(target=_heartbeat, name="gpu-lock-heartbeat", daemon=True)
        hb_thread.start()

    proc = subprocess.Popen(cmd, env=env)

    def _forward(signum, _frame):
        try:
            proc.send_signal(signum)
        except ProcessLookupError:
            pass

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, _forward)
        except (ValueError, OSError):
            pass

    try:
        rc = proc.wait()
    finally:
        stop_flag.set()
        if hb_thread is not None:
            hb_thread.join(timeout=5)
        release_sync(lease)
        click.echo(f"[gpu-lock] released lease={lease.lease_id}", err=True)

    sys.exit(rc)


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------

@cli.command(help="Show status of all GPUs, or one with --gpu.")
@click.option("--gpu", type=int, default=None)
def status(gpu: Optional[int]) -> None:
    data = _http_get("/status", gpu=gpu)
    click.echo(json.dumps(data, indent=2, ensure_ascii=False))


@cli.command(help="Show queue length/busy flag.")
@click.option("--gpu", type=int, default=None)
def queue(gpu: Optional[int]) -> None:
    data = _http_get("/queue", gpu=gpu)
    click.echo(json.dumps(data, indent=2, ensure_ascii=False))


@cli.command(help="Raw acquire — prints the lease JSON; does not run a command.")
@click.option("--gpu", required=True)
@click.option("--owner", default=None)
@click.option("--ttl", type=float, default=300)
@click.option("--wait-timeout", type=float, default=300)
@click.option("--priority",
              type=click.Choice([p.value for p in Priority], case_sensitive=False),
              default=Priority.NORMAL.value)
def acquire(gpu: str, owner: Optional[str], ttl: float, wait_timeout: float, priority: str) -> None:
    lease = acquire_sync(owner or socket.gethostname(), gpu, ttl=ttl,
                         wait_timeout=wait_timeout, priority=priority)
    if lease is None:
        click.echo("(passthrough — GPU_LOCK_URL empty or service unreachable)", err=True)
        raise SystemExit(1)
    click.echo(json.dumps({
        "lease_id": lease.lease_id,
        "owner": lease.owner,
        "gpu": lease.gpu,
        "priority": lease.priority.value,
        "ttl": lease.ttl,
        "expires_at": lease.expires_at,
    }, indent=2))


@cli.command(help="Release a lease by id.")
@click.argument("lease_id")
def release(lease_id: str) -> None:
    _http_post(f"/release/{lease_id}")
    click.echo("released")


@cli.command(help="Renew a lease. Extends TTL from now.")
@click.argument("lease_id")
@click.option("--ttl", type=float, default=None, help="New TTL in seconds.")
def renew(lease_id: str, ttl: Optional[float]) -> None:
    data = _http_post(f"/renew/{lease_id}", ttl=ttl)
    click.echo(json.dumps(data, indent=2, ensure_ascii=False))


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
