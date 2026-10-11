"""Typed models for the two things callers work with most: a session
(one run of something) and a job's current state. Everything else the
SDK returns is Veeam's own JSON as dictionaries.

Both keep Veeam's record untouched in `raw`, and both ignore fields they
do not name, so a server that sends more than this was written against
does not break parsing.
"""

from __future__ import annotations

from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

# A session in any state but this one has not finished.
STOPPED = "Stopped"


class Session(BaseModel):
    """One run: of a backup job, a restore, a configuration backup, ...

    In Veeam a *job* is a definition and each run of it is a *session*.
    A session's `state` says whether it is still going (Starting,
    Working, Postprocessing, ... or Stopped); its `result` says how it
    ended (None while running, then Success, Warning or Failed).
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    name: str = ""
    session_type: str | None = Field(default=None, alias="sessionType")
    state: str = ""
    job_id: str | None = Field(default=None, alias="jobId")
    creation_time: AwareDatetime | None = Field(default=None, alias="creationTime")
    end_time: AwareDatetime | None = Field(default=None, alias="endTime")
    progress_percent: int | None = Field(default=None, alias="progressPercent")
    result: str | None = None
    message: str | None = None
    is_canceled: bool | None = None
    platform_name: str | None = Field(default=None, alias="platformName")
    parent_session_id: str | None = Field(default=None, alias="parentSessionId")
    initiated_by: str | None = Field(default=None, alias="initiatedBy")
    # Veeam's update sequence number: it goes up whenever the session's
    # record changes, so it can tell a changed row from an unchanged one.
    usn: int | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.state != STOPPED


def session_from(raw: dict[str, Any]) -> Session:
    """Veeam nests how a session ended as result{result, message,
    isCanceled}; this lifts those three to the top."""
    outcome = raw.get("result")
    flat: dict[str, Any] = {key: value for key, value in raw.items() if key != "result"}
    if isinstance(outcome, dict):
        flat["result"] = outcome.get("result")
        flat["message"] = outcome.get("message")
        flat["is_canceled"] = outcome.get("isCanceled")
    else:
        flat["result"] = outcome
    return Session.model_validate({**flat, "raw": raw})


class JobState(BaseModel):
    """A job as it stands now: what it is, when it last ran and how that
    went, and when it runs next (GET /api/v1/jobs/states)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    name: str = ""
    type: str | None = None
    status: str | None = None
    last_run: AwareDatetime | None = Field(default=None, alias="lastRun")
    last_result: str | None = Field(default=None, alias="lastResult")
    next_run: AwareDatetime | None = Field(default=None, alias="nextRun")
    workload: str | None = None
    objects_count: int | None = Field(default=None, alias="objectsCount")
    repository_id: str | None = Field(default=None, alias="repositoryId")
    repository_name: str | None = Field(default=None, alias="repositoryName")
    session_id: str | None = Field(default=None, alias="sessionId")
    description: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


def job_state_from(raw: dict[str, Any]) -> JobState:
    return JobState.model_validate({**raw, "raw": raw})
