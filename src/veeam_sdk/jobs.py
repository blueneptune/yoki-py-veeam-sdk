"""Jobs: the definitions, and what each one's last run did."""

from __future__ import annotations

from collections.abc import Sequence

from sdk_primer import NotFoundError

from ._base import Entities, Entity, Resource
from .models import JobState, job_state_from

JobStates = list[JobState]


class Jobs(Resource):
    def list(self, *, name: str | None = None, types: Sequence[str] | None = None) -> Entities:
        """Every job's definition (GET /api/v1/jobs): id, name, type,
        isDisabled, and the job's settings."""
        return self._all(
            "/api/v1/jobs", {"nameFilter": name, "typeFilter": list(types) if types else None}
        )

    def get(self, job_id: str) -> Entity | None:
        try:
            job: Entity = self._get(f"/api/v1/jobs/{job_id}")
        except NotFoundError:
            return None
        return job

    def states(self, *, name: str | None = None) -> JobStates:
        """Every job as it stands now (GET /api/v1/jobs/states): last
        run, last result, next run, how many objects it covers."""
        return [
            job_state_from(raw) for raw in self._all("/api/v1/jobs/states", {"nameFilter": name})
        ]

    # -- changes: these start, stop or switch off real work ----------------

    def start(self, job_id: str) -> Entity | None:
        """Start a job. Returns the session Veeam created for the run."""
        session: Entity | None = self._change("POST", f"/api/v1/jobs/{job_id}/start")
        return session

    def stop(self, job_id: str) -> None:
        self._change("POST", f"/api/v1/jobs/{job_id}/stop")

    def retry(self, job_id: str) -> Entity | None:
        session: Entity | None = self._change("POST", f"/api/v1/jobs/{job_id}/retry")
        return session

    def enable(self, job_id: str) -> None:
        self._change("POST", f"/api/v1/jobs/{job_id}/enable")

    def disable(self, job_id: str) -> None:
        self._change("POST", f"/api/v1/jobs/{job_id}/disable")
