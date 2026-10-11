"""The facade: the read-only rule, what each changing call sends, the
inventory groups, and the probe."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx
from conftest import SERVER, page
from sdk_primer import SDKError

from veeam_sdk import ChangesNotAllowed, VeeamClient
from veeam_sdk.probe import run_probe


def test_a_default_client_refuses_every_call_that_changes_the_server(
    vbr_api: respx.MockRouter,
) -> None:
    with VeeamClient(SERVER, "u", "p") as vbr:
        changes = [
            lambda: vbr.jobs.start("j"),
            lambda: vbr.jobs.stop("j"),
            lambda: vbr.jobs.retry("j"),
            lambda: vbr.jobs.enable("j"),
            lambda: vbr.jobs.disable("j"),
            lambda: vbr.sessions.stop("s"),
        ]
        for change in changes:
            with pytest.raises(ChangesNotAllowed):
                change()
    # Refused before anything was sent - not even a login.
    assert vbr_api.calls.call_count == 0


def test_with_changes_allowed_each_call_goes_to_its_endpoint(vbr_api: respx.MockRouter) -> None:
    start = vbr_api.post("/api/v1/jobs/j/start").respond(json={"id": "sess-9"})
    others = [
        vbr_api.post(f"/api/v1/jobs/j/{verb}").respond(200) for verb in ("stop", "retry", "enable")
    ]
    disable = vbr_api.post("/api/v1/jobs/j/disable").respond(200)
    stop = vbr_api.post("/api/v1/sessions/s/stop").respond(200)
    with VeeamClient(SERVER, "u", "p", allow_changes=True) as vbr:
        assert vbr.jobs.start("j") == {"id": "sess-9"}
        vbr.jobs.stop("j")
        vbr.jobs.retry("j")
        vbr.jobs.enable("j")
        vbr.jobs.disable("j")
        vbr.sessions.stop("s")
    assert all(route.call_count == 1 for route in [start, *others, disable, stop])


def test_a_change_that_times_out_is_not_sent_again(vbr_api: respx.MockRouter) -> None:
    start = vbr_api.post("/api/v1/jobs/j/start").mock(side_effect=httpx.ReadTimeout("slow"))
    with VeeamClient(SERVER, "u", "p", allow_changes=True) as vbr:
        with pytest.raises(SDKError):
            vbr.jobs.start("j")
    # The server may have started the job; a second request would start it twice.
    assert start.call_count == 1


def test_job_states_are_typed_and_keep_veeams_record(vbr_api: respx.MockRouter) -> None:
    raw = {
        "id": "j1",
        "name": "Daily VMs",
        "type": "VSphereBackup",
        "status": "inactive",
        "lastRun": "2026-10-10T22:00:00Z",
        "lastResult": "Success",
        "nextRun": None,
        "workload": "vm",
        "objectsCount": 12,
        "repositoryName": "Repo 1",
        "sessionId": "s1",
    }
    vbr_api.get("/api/v1/jobs/states").respond(json=page([raw]))
    with VeeamClient(SERVER, "u", "p") as vbr:
        (state,) = vbr.jobs.states()
    assert (state.last_result, state.objects_count, state.next_run) == ("Success", 12, None)
    assert state.last_run == datetime(2026, 10, 10, 22, tzinfo=UTC)
    assert state.raw == raw


def test_inventory_listings_and_the_newest_restore_point(vbr_api: respx.MockRouter) -> None:
    vbr_api.get("/api/v1/backups").respond(json=page([{"id": "b1"}]))
    vbr_api.get("/api/v1/backups/b1/backupObjects").respond(json=page([{"id": "o1"}]))
    objects = vbr_api.get("/api/v1/backupObjects").respond(
        json=page([{"id": "o1", "name": "vm01"}])
    )
    points = vbr_api.get("/api/v1/backupObjects/o1/restorePoints").respond(
        json=page([{"id": "rp9", "creationTime": "2026-10-10T22:10:00Z"}])
    )
    vbr_api.get("/api/v1/backupObjects/none").respond(404)
    vbr_api.get("/api/v1/repositories/states").respond(json=page([{"id": "r1", "freeGB": 10.5}]))
    vbr_api.get("/api/v1/managedServers").respond(json=page([{"id": "m1"}]))
    with VeeamClient(SERVER, "u", "p") as vbr:
        assert vbr.backups.list(job_id="j1") == [{"id": "b1"}]
        assert vbr.backups.objects("b1") == [{"id": "o1"}]
        assert vbr.backup_objects.list(platform="VMware")[0]["name"] == "vm01"
        newest = vbr.backup_objects.latest_restore_point("o1")
        assert newest is not None and newest["id"] == "rp9"
        assert vbr.backup_objects.get("none") is None
        assert vbr.repositories.states()[0]["freeGB"] == 10.5
        assert vbr.managed_servers.list() == [{"id": "m1"}]

    assert objects.calls[0].request.url.params["platformNameFilter"] == "VMware"
    asked = points.calls[0].request.url.params
    assert (asked["limit"], asked["orderColumn"], asked["orderAsc"]) == (
        "1",
        "CreationTime",
        "false",
    )


def test_the_probe_reports_each_call_and_carries_on_past_a_failure(
    vbr_api: respx.MockRouter,
) -> None:
    session = {"id": "s1", "sessionType": "BackupJob", "state": "Stopped"}
    vbr_api.get("/api/v1/serverInfo").respond(json={"name": "vbr01", "buildVersion": "13.0.1"})
    vbr_api.get("/api/v1/sessions").respond(json=page([session], total=40))
    vbr_api.get("/api/v1/sessions/s1").respond(json=session)
    vbr_api.get("/api/v1/sessions/s1/taskSessions").respond(404)  # older API revision
    vbr_api.get("/api/v1/jobs/states").respond(json=page([]))
    vbr_api.get("/api/v1/jobs").respond(json=page([]))
    vbr_api.get("/api/v1/backups").respond(json=page([]))
    vbr_api.get("/api/v1/backupObjects").respond(json=page([]))
    vbr_api.get("/api/v1/repositories/states").respond(json=page([]))
    vbr_api.get("/api/v1/managedServers").respond(json=page([]))
    vbr_api.get("/api/v1/agents/discoveredEntities").respond(403)

    lines: list[str] = []
    with VeeamClient(SERVER, "u", "p") as vbr:
        failed = run_probe(vbr, lines.append)

    assert failed == 2
    by_name = {line[8:38].strip(): line for line in lines}
    assert by_name["server info"].startswith("ok") and "13.0.1" in by_name["server info"]
    assert "40 sessions" in by_name["session listing"] and "BackupJob" in by_name["session listing"]
    assert by_name["task sessions"].startswith("FAILED")
    assert by_name["agent computers"].startswith("FAILED")
    assert "skipped" in by_name["latest restore point"]
    assert len(lines) == 14
