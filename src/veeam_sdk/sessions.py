"""Sessions: the runs. This is where job history comes from."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import datetime
from typing import Any

from loguru import logger
from pydantic import ValidationError
from sdk_primer import NotFoundError

from ._base import Entities, Resource
from .models import Session, session_from


def parse_session(raw: dict[str, Any]) -> Session | None:
    """A Session, or None if this record cannot be read. One odd record
    among thousands should cost that record, loudly, not the whole run."""
    try:
        return session_from(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        logger.error("Skipping session {}: could not read it - {}", raw.get("id"), problems)
        return None


class Sessions(Resource):
    def _filters(
        self,
        *,
        created_after: datetime | None,
        created_before: datetime | None,
        ended_after: datetime | None,
        ended_before: datetime | None,
        types: Sequence[str] | None,
        state: str | None,
        results: Sequence[str] | None,
        job_id: str | None,
        name: str | None,
        order_column: str,
        ascending: bool,
    ) -> dict[str, Any]:
        return {
            "createdAfterFilter": created_after,
            "createdBeforeFilter": created_before,
            "endedAfterFilter": ended_after,
            "endedBeforeFilter": ended_before,
            "typeFilter": list(types) if types else None,
            "stateFilter": state,
            "resultFilter": list(results) if results else None,
            "jobIdFilter": job_id,
            "nameFilter": name,
            "orderColumn": order_column,
            "orderAsc": ascending,
        }

    def iter_pages(
        self,
        *,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        ended_after: datetime | None = None,
        ended_before: datetime | None = None,
        types: Sequence[str] | None = None,
        state: str | None = None,
        results: Sequence[str] | None = None,
        job_id: str | None = None,
        name: str | None = None,
        order_column: str = "CreationTime",
        ascending: bool = True,
    ) -> Iterator[list[Session]]:
        """Sessions matching the filters, a page at a time
        (GET /api/v1/sessions).

        Oldest first by creation time unless told otherwise, so sessions
        that start while this is running land after the last page rather
        than shifting earlier ones.

        `types` are Veeam's session types (BackupJob, ReplicaJob,
        ConfigurationBackup, RestoreVm, ...); `state` is one of Stopped,
        Starting, Working, ...; `results` any of None, Success, Warning,
        Failed. The time filters take timezone-aware datetimes.
        """
        params = self._filters(
            created_after=created_after,
            created_before=created_before,
            ended_after=ended_after,
            ended_before=ended_before,
            types=types,
            state=state,
            results=results,
            job_id=job_id,
            name=name,
            order_column=order_column,
            ascending=ascending,
        )
        for page in self._pages("/api/v1/sessions", params):
            sessions = [s for raw in page if (s := parse_session(raw)) is not None]
            if sessions:
                yield sessions

    def sample(
        self,
        limit: int = 5,
        *,
        created_after: datetime | None = None,
        ended_after: datetime | None = None,
        ended_before: datetime | None = None,
    ) -> tuple[int | None, list[Session]]:
        """A few sessions, newest first, and the server's count of how
        many match - one request, for checking the listing works."""
        params = self._filters(
            created_after=created_after,
            created_before=None,
            ended_after=ended_after,
            ended_before=ended_before,
            types=None,
            state=None,
            results=None,
            job_id=None,
            name=None,
            order_column="CreationTime",
            ascending=False,
        )
        items, total = self._page("/api/v1/sessions", params, 0, limit)
        return total, [s for raw in items if (s := parse_session(raw)) is not None]

    def oldest_start(self) -> datetime | None:
        """When the oldest session the server still has was created - the
        point behind which there is no history to fetch."""
        params = {"orderColumn": "CreationTime", "orderAsc": True}
        items, _ = self._page("/api/v1/sessions", params, 0, 1)
        for raw in items:
            session = parse_session(raw)
            if session is not None:
                return session.creation_time
        return None

    def get(self, session_id: str) -> Session | None:
        """One session (GET /api/v1/sessions/{id}), or None if the server
        no longer has it."""
        try:
            raw = self._get(f"/api/v1/sessions/{session_id}")
        except NotFoundError:
            return None
        return parse_session(raw)

    def logs(self, session_id: str) -> Entities:
        """A session's log lines (GET /api/v1/sessions/{id}/logs): each
        with status, title, description, startTime, updateTime."""
        body = self._get(f"/api/v1/sessions/{session_id}/logs")
        records: Entities = body.get("records") or []
        return records

    def task_sessions(self, session_id: str) -> Entities:
        """The per-object tasks inside a session
        (GET /api/v1/sessions/{id}/taskSessions) - for a backup job, one
        per machine it processed, each with its own state and result.
        Needs a newer API revision than the session listing does."""
        return self._all(f"/api/v1/sessions/{session_id}/taskSessions")

    # -- changes -----------------------------------------------------------

    def stop(self, session_id: str) -> None:
        self._change("POST", f"/api/v1/sessions/{session_id}/stop")
