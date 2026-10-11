"""Logging in, staying logged in, and logging out."""

from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest
import respx
from conftest import SERVER, TOKEN, page
from sdk_primer import AuthenticationError

from veeam_sdk import VeeamClient
from veeam_sdk.auth import PasswordAuth


def form(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.content.decode())


def test_login_is_a_form_post_and_the_token_rides_on_later_calls(
    vbr_api: respx.MockRouter,
) -> None:
    info = vbr_api.get("/api/v1/serverInfo").respond(json={"name": "vbr01"})
    with VeeamClient(SERVER, "LAB\\svc", "p@ss word") as vbr:
        assert vbr.server.info() == {"name": "vbr01"}
        vbr.server.info()

    token = vbr_api.routes[0]
    assert token.call_count == 1  # one login serves both calls
    login = token.calls[0].request
    assert login.headers["content-type"] == "application/x-www-form-urlencoded"
    assert login.headers["x-api-version"] == "1.1-rev0"
    assert form(login) == {
        "grant_type": ["password"],
        "username": ["LAB\\svc"],
        "password": ["p@ss word"],
    }
    sent = info.calls[0].request
    assert sent.headers["authorization"] == "Bearer access-1"
    assert sent.headers["x-api-version"] == "1.1-rev0"


def test_closing_logs_out_with_the_access_token(vbr_api: respx.MockRouter) -> None:
    vbr_api.get("/api/v1/serverInfo").respond(json={})
    with VeeamClient(SERVER, "u", "p") as vbr:
        vbr.server.info()
    logout = vbr_api.routes[1]
    assert logout.call_count == 1
    assert logout.calls[0].request.headers["authorization"] == "Bearer access-1"


def test_a_client_that_never_logged_in_has_nothing_to_log_out_of(
    vbr_api: respx.MockRouter,
) -> None:
    VeeamClient(SERVER, "u", "p").close()
    assert vbr_api.routes[0].call_count == 0 and vbr_api.routes[1].call_count == 0


def test_a_refused_login_is_an_authentication_error_without_the_password(
    vbr_api: respx.MockRouter,
) -> None:
    vbr_api.post("/api/oauth2/token").respond(400, json={"error": "invalid_grant"})
    with VeeamClient(SERVER, "u", "hunter2") as vbr:
        with pytest.raises(AuthenticationError) as caught:
            vbr.server.info()
    assert "hunter2" not in str(caught.value)


def test_a_401_is_answered_by_refreshing_and_retrying_once(vbr_api: respx.MockRouter) -> None:
    vbr_api.post("/api/oauth2/token").mock(
        side_effect=[
            httpx.Response(200, json=TOKEN),
            httpx.Response(200, json={**TOKEN, "access_token": "access-2"}),
        ]
    )
    vbr_api.get("/api/v1/serverInfo").respond(json={})
    states = vbr_api.get("/api/v1/jobs/states").mock(
        side_effect=[httpx.Response(401), httpx.Response(200, json=page([]))]
    )
    with VeeamClient(SERVER, "u", "p") as vbr:
        vbr.server.info()  # logs in
        assert vbr.jobs.states() == []

    token = vbr_api.routes[0]
    assert form(token.calls[1].request) == {
        "grant_type": ["refresh_token"],
        "refresh_token": ["refresh-1"],
    }
    assert states.calls[1].request.headers["authorization"] == "Bearer access-2"


@respx.mock
def test_the_token_is_renewed_before_it_runs_out_and_login_is_the_fallback() -> None:
    clock = [0.0]
    token = respx.post(f"{SERVER}/api/oauth2/token").mock(
        side_effect=[
            httpx.Response(200, json=TOKEN),
            httpx.Response(200, json={**TOKEN, "access_token": "access-2"}),
            httpx.Response(400, json={"error": "invalid_grant"}),  # refresh token expired
            httpx.Response(200, json={**TOKEN, "access_token": "access-3"}),
        ]
    )
    seen = respx.get(f"{SERVER}/x").respond(json={})
    auth = PasswordAuth(SERVER, "u", "p", client=httpx.Client(), now=lambda: clock[0])
    with httpx.Client(auth=auth) as client:
        client.get(f"{SERVER}/x")
        clock[0] = 800.0  # inside the 15 minutes, outside the margin
        client.get(f"{SERVER}/x")
        clock[0] = 850.0  # within a minute of expiry: refresh
        client.get(f"{SERVER}/x")
        clock[0] = 5000.0  # refresh refused: log in again
        client.get(f"{SERVER}/x")

    used = [call.request.headers["authorization"] for call in seen.calls]
    assert used == ["Bearer access-1", "Bearer access-1", "Bearer access-2", "Bearer access-3"]
    grants = [form(call.request)["grant_type"][0] for call in token.calls]
    assert grants == ["password", "refresh_token", "refresh_token", "password"]
