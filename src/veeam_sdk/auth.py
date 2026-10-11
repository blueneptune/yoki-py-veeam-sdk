"""Logging in to Veeam Backup & Replication.

VBR uses OAuth 2.0's password grant: POST the user name and password,
form-encoded, to /api/oauth2/token and get back an access token (good
for 15 minutes) and a refresh token (good for longer; the reference says
24 hours, and a field report says v13 differs). Every later request
carries `Authorization: Bearer <access token>`.

This keeps a session alive without the caller thinking about it: it logs
in on first use, trades the refresh token for a new pair shortly before
the access token runs out or when a request comes back 401, and falls
back to logging in again if the refresh token is no longer accepted.
Tokens live in memory only. Nothing is written to disk, and nothing
needs to be: the password is what a run starts from each time.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Generator

import httpx
from loguru import logger
from sdk_primer import AuthenticationError

TOKEN_PATH = "/api/oauth2/token"
LOGOUT_PATH = "/api/oauth2/logout"


class PasswordAuth(httpx.Auth):
    def __init__(
        self,
        server_url: str,
        username: str,
        password: str,
        *,
        client: httpx.Client,
        renew_margin: float = 60.0,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._token_url = server_url.rstrip("/") + TOKEN_PATH
        self._logout_url = server_url.rstrip("/") + LOGOUT_PATH
        self._username = username
        self._password = password
        self._client = client
        self._renew_margin = renew_margin
        self._now = now
        self._access: str | None = None
        self._refresh: str | None = None
        self._expires_at = 0.0

    def _request_token(self, form: dict[str, str]) -> httpx.Response:
        # `data=` sends application/x-www-form-urlencoded, which is what
        # the token endpoint takes, and encodes a DOMAIN\\user backslash.
        return self._client.post(self._token_url, data=form)

    def _store(self, response: httpx.Response) -> None:
        body = response.json()
        self._access = str(body["access_token"])
        self._refresh = body.get("refresh_token")
        self._expires_at = self._now() + float(body.get("expires_in", 900))

    def login(self) -> None:
        logger.info("Logging in to Veeam as {}", self._username)
        response = self._request_token(
            {"grant_type": "password", "username": self._username, "password": self._password}
        )
        if response.status_code >= 400:
            raise AuthenticationError(
                f"Veeam login failed for {self._username}: HTTP {response.status_code} "
                f"{response.text[:200]}"
            )
        self._store(response)

    def renew(self) -> None:
        """A new token pair: by refresh token if there is one that still
        works, by logging in again if not."""
        if self._refresh:
            response = self._request_token(
                {"grant_type": "refresh_token", "refresh_token": self._refresh}
            )
            if response.status_code < 400:
                self._store(response)
                return
            logger.info("Veeam refresh token was not accepted; logging in again")
        self.login()

    def logout(self) -> None:
        """End the session on the server. Best effort."""
        if self._access is None:
            return
        try:
            self._client.post(self._logout_url, headers={"Authorization": f"Bearer {self._access}"})
        except httpx.HTTPError:
            pass
        self._access = self._refresh = None

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response]:
        renewed = False
        if self._access is None:
            self.login()
            renewed = True
        elif self._now() >= self._expires_at - self._renew_margin:
            self.renew()
            renewed = True
        request.headers["Authorization"] = f"Bearer {self._access}"
        response = yield request

        if response.status_code == 401 and not renewed:
            logger.warning("Veeam answered 401; renewing the session and retrying once")
            self.renew()
            request.headers["Authorization"] = f"Bearer {self._access}"
            yield request
