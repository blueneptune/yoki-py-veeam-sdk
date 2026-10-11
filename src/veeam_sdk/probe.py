"""veeam-probe: try each call the SDK makes, once, and say what happened.

The SDK was written from Veeam's documentation without a server to run
against. This is the quickest way to find out where the two differ: it
only reads, stores nothing, and prints one line per call.

    VEEAM_URL=https://vbr01.example.com:9419 \\
    VEEAM_USERNAME='DOMAIN\\svc_blt' VEEAM_PASSWORD=... veeam-probe

Optional: VEEAM_API_VERSION (default 1.1-rev0), VEEAM_VERIFY_TLS=false
for a self-signed certificate, VEEAM_CA_BUNDLE=/path/to/ca.pem.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .client import DEFAULT_API_VERSION, VeeamClient

Check = tuple[str, Callable[[], str]]


def checks(vbr: VeeamClient) -> list[Check]:
    day_ago = datetime.now(UTC) - timedelta(hours=24)
    now = datetime.now(UTC)
    found: dict[str, str] = {}

    def server_info() -> str:
        info = vbr.server.info()
        return f"{info.get('name')} build {info.get('buildVersion')}"

    def sessions_recent() -> str:
        total, sessions = vbr.sessions.sample(created_after=day_ago)
        if sessions:
            found["session"] = sessions[0].id
        kinds = sorted({s.session_type or "?" for s in sessions})
        return f"{total} sessions created in the last 24 h; types seen: {', '.join(kinds) or '-'}"

    def sessions_by_end() -> str:
        total, _ = vbr.sessions.sample(ended_after=day_ago, ended_before=now)
        return f"{total} sessions ended in the last 24 h"

    def oldest() -> str:
        return f"oldest session created {vbr.sessions.oldest_start()}"

    def one_session() -> str:
        if "session" not in found:
            return "skipped: no session to fetch"
        session = vbr.sessions.get(found["session"])
        return f"state {session.state}, result {session.result}" if session else "not found"

    def task_sessions() -> str:
        if "session" not in found:
            return "skipped: no session to fetch"
        return f"{len(vbr.sessions.task_sessions(found['session']))} task sessions"

    def job_states() -> str:
        states = vbr.jobs.states()
        kinds = sorted({s.type or "?" for s in states})
        return f"{len(states)} jobs; types: {', '.join(kinds) or '-'}"

    def backup_objects() -> str:
        objects = next(iter(vbr.backup_objects.iter_pages()), [])
        if objects:
            found["object"] = str(objects[0].get("id"))
        return f"{len(objects)} objects on the first page"

    def latest_point() -> str:
        if "object" not in found:
            return "skipped: no backup object"
        point = vbr.backup_objects.latest_restore_point(found["object"])
        return f"newest restore point {point.get('creationTime') if point else None}"

    return [
        ("server info", server_info),
        ("session listing", sessions_recent),
        ("session listing by end time", sessions_by_end),
        ("oldest session", oldest),
        ("one session", one_session),
        ("task sessions", task_sessions),
        ("job states", job_states),
        ("job definitions", lambda: f"{len(vbr.jobs.list())} jobs"),
        ("backups", lambda: f"{len(vbr.backups.list())} backups"),
        ("backup objects", backup_objects),
        ("latest restore point", latest_point),
        ("repository states", lambda: f"{len(vbr.repositories.states())} repositories"),
        ("managed servers", lambda: f"{len(vbr.managed_servers.list())} servers"),
        ("agent computers", lambda: f"{len(vbr.agents.computers())} computers"),
    ]


def run_probe(vbr: VeeamClient, out: Callable[[str], None] = print) -> int:
    """Run every check; return how many failed."""
    failed = 0
    for name, check in checks(vbr):
        try:
            out(f"ok      {name:<30} {check()}")
        except Exception as exc:  # noqa: BLE001 - reporting every failure is the point
            failed += 1
            out(f"FAILED  {name:<30} {type(exc).__name__}: {str(exc)[:160]}")
    return failed


def main() -> int:
    env = os.environ
    missing = [k for k in ("VEEAM_URL", "VEEAM_USERNAME", "VEEAM_PASSWORD") if not env.get(k)]
    if missing:
        print(
            f"Set {', '.join(missing)}. See `veeam_sdk.probe` for the full list.", file=sys.stderr
        )
        return 2
    ca = env.get("VEEAM_CA_BUNDLE")
    with VeeamClient(
        env["VEEAM_URL"],
        env["VEEAM_USERNAME"],
        env["VEEAM_PASSWORD"],
        api_version=env.get("VEEAM_API_VERSION", DEFAULT_API_VERSION),
        verify_tls=env.get("VEEAM_VERIFY_TLS", "true").lower() not in ("false", "0", "no"),
        ca_bundle=Path(ca) if ca else None,
    ) as vbr:
        return 1 if run_probe(vbr) else 0


if __name__ == "__main__":
    sys.exit(main())
