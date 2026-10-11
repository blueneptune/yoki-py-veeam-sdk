"""Sessions: paging, filters, and reading Veeam's record into a Session."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
import respx
from conftest import SERVER, page

from veeam_sdk import VeeamClient, VeeamError
from veeam_sdk.models import session_from

RAW = {
    "id": "5a6b7c8d-0000-0000-0000-000000000001",
    "name": "Daily VMs",
    "sessionType": "BackupJob",
    "state": "Stopped",
    "jobId": "11111111-0000-0000-0000-000000000001",
    "creationTime": "2026-10-10T22:00:03.117+02:00",
    "endTime": "2026-10-10T22:14:40.5+02:00",
    "progressPercent": 100,
    "result": {"result": "Warning", "message": "1 VM skipped", "isCanceled": False},
    "usn": 4242,
    "platformName": "VMware",
    "somethingNewerServersSend": {"x": 1},
}


def test_a_session_is_read_with_its_result_lifted_and_the_original_kept() -> None:
    session = session_from(RAW)
    assert (session.result, session.message, session.is_canceled) == (
        "Warning",
        "1 VM skipped",
        False,
    )
    assert session.session_type == "BackupJob" and session.usn == 4242
    assert session.creation_time == datetime(
        2026, 10, 10, 22, 0, 3, 117000, tzinfo=timezone(timedelta(hours=2))
    )
    assert not session.is_active
    assert session.raw == RAW


def test_a_running_session_has_no_result_or_end_yet() -> None:
    running = {**RAW, "state": "Working", "endTime": None, "result": {"result": "None"}}
    session = session_from(running)
    assert session.is_active and session.end_time is None and session.result == "None"


def test_listing_walks_every_page_and_stops_on_a_short_one(vbr_api: respx.MockRouter) -> None:
    def numbered(n: int) -> dict:
        return {**RAW, "id": f"s{n}"}

    route = vbr_api.get("/api/v1/sessions").mock(
        side_effect=lambda request: respx.MockResponse(
            200,
            json=page(
                [numbered(n) for n in range(5)][
                    int(request.url.params["skip"]) : int(request.url.params["skip"]) + 2
                ],
                total=5,
            ),
        )
    )
    with VeeamClient(SERVER, "u", "p", page_size=2) as vbr:
        pages = list(vbr.sessions.iter_pages())

    assert [[s.id for s in p] for p in pages] == [["s0", "s1"], ["s2", "s3"], ["s4"]]
    assert [c.request.url.params["skip"] for c in route.calls] == ["0", "2", "4"]
    assert all(c.request.url.params["limit"] == "2" for c in route.calls)


def test_filters_are_sent_the_way_veeam_reads_them(vbr_api: respx.MockRouter) -> None:
    route = vbr_api.get("/api/v1/sessions").respond(json=page([]))
    since = datetime(2026, 10, 1, tzinfo=UTC)
    with VeeamClient(SERVER, "u", "p") as vbr:
        list(
            vbr.sessions.iter_pages(
                ended_after=since,
                ended_before=since + timedelta(days=1),
                types=["BackupJob", "ReplicaJob"],
                results=["Failed"],
                state="Stopped",
            )
        )
    params = route.calls[0].request.url.params
    assert params["endedAfterFilter"] == "2026-10-01T00:00:00+00:00"
    assert params["endedBeforeFilter"] == "2026-10-02T00:00:00+00:00"
    assert params.get_list("typeFilter") == ["BackupJob", "ReplicaJob"]
    assert params.get_list("resultFilter") == ["Failed"]
    assert params["stateFilter"] == "Stopped"
    # Oldest first, so sessions starting mid-walk land after the last page.
    assert (params["orderColumn"], params["orderAsc"]) == ("CreationTime", "true")
    assert "createdAfterFilter" not in params and "jobIdFilter" not in params


def test_a_time_without_a_zone_is_refused_rather_than_guessed(vbr_api: respx.MockRouter) -> None:
    with VeeamClient(SERVER, "u", "p") as vbr:
        with pytest.raises(ValueError, match="timezone-aware"):
            list(vbr.sessions.iter_pages(created_after=datetime(2026, 10, 1)))  # noqa: DTZ001


def test_one_unreadable_session_costs_only_itself(vbr_api: respx.MockRouter) -> None:
    broken = {**RAW, "id": "bad", "creationTime": "not a time"}
    vbr_api.get("/api/v1/sessions").respond(json=page([RAW, broken]))
    with VeeamClient(SERVER, "u", "p") as vbr:
        (only,) = list(vbr.sessions.iter_pages())
    assert [s.id for s in only] == [RAW["id"]]


def test_get_returns_none_for_a_session_the_server_no_longer_has(
    vbr_api: respx.MockRouter,
) -> None:
    vbr_api.get("/api/v1/sessions/here").respond(json=RAW)
    vbr_api.get("/api/v1/sessions/gone").respond(404, json={"errorCode": "NotFound"})
    with VeeamClient(SERVER, "u", "p") as vbr:
        found = vbr.sessions.get("here")
        assert found is not None and found.name == "Daily VMs"
        assert vbr.sessions.get("gone") is None


def test_sample_and_oldest_start_each_cost_one_request(vbr_api: respx.MockRouter) -> None:
    route = vbr_api.get("/api/v1/sessions").respond(json=page([RAW], total=731))
    with VeeamClient(SERVER, "u", "p") as vbr:
        total, sessions = vbr.sessions.sample(3)
        oldest = vbr.sessions.oldest_start()
    assert (total, len(sessions)) == (731, 1) and oldest == session_from(RAW).creation_time
    newest_first, oldest_first = (c.request.url.params for c in route.calls)
    assert (newest_first["limit"], newest_first["orderAsc"]) == ("3", "false")
    assert (oldest_first["limit"], oldest_first["orderAsc"]) == ("1", "true")


def test_an_answer_that_is_not_a_listing_is_an_error_not_an_empty_result(
    vbr_api: respx.MockRouter,
) -> None:
    vbr_api.get("/api/v1/sessions").respond(json={"errorCode": "Unknown", "message": "nope"})
    with VeeamClient(SERVER, "u", "p") as vbr:
        with pytest.raises(VeeamError, match="paged listing"):
            list(vbr.sessions.iter_pages())


def test_logs_and_task_sessions(vbr_api: respx.MockRouter) -> None:
    vbr_api.get("/api/v1/sessions/s1/logs").respond(
        json={"totalRecords": 1, "records": [{"id": 1, "status": "Succeeded", "title": "Done"}]}
    )
    tasks = [{"id": "t1", "name": "vm01"}]
    vbr_api.get("/api/v1/sessions/s1/taskSessions").respond(json=page(tasks))
    with VeeamClient(SERVER, "u", "p") as vbr:
        assert vbr.sessions.logs("s1")[0]["title"] == "Done"
        assert vbr.sessions.task_sessions("s1") == tasks
