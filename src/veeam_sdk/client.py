"""The SDK's entry point: VeeamClient.

A facade: one object per backup server, with a named group of calls for
each kind of thing - `vbr.sessions`, `vbr.jobs`, `vbr.backups`,
`vbr.backup_objects`, `vbr.restore_points`, `vbr.repositories`,
`vbr.managed_servers`, `vbr.agents`, `vbr.server`.

    with VeeamClient("https://vbr01.example.com:9419", "svc_blt", password) as vbr:
        vbr.server.info()["buildVersion"]
        for page in vbr.sessions.iter_pages(created_after=since):
            ...

sdk-primer supplies the transport underneath (retry and backoff, typed
exceptions, per-request log correlation). Its BaseAPIModel and
ResourceManager are not used: Veeam's listings are paged with skip and
limit inside a {"data", "pagination"} envelope and filtered with
`...Filter` query parameters, which each group handles through
_base.Resource.

Written from Veeam's REST API reference. **None of it has been run
against a Veeam server.**
"""

from __future__ import annotations

import ssl
from pathlib import Path

import httpx
from sdk_primer import APIClient

from .auth import PasswordAuth
from .inventory import (
    Agents,
    BackupObjects,
    Backups,
    ManagedServers,
    Repositories,
    RestorePoints,
    Server,
)
from .jobs import Jobs
from .sessions import Sessions

# Sent on every request as `x-api-version`; the server rejects a request
# without it. Each VBR release adds revisions and keeps accepting older
# ones for a time: 1.1-rev0 came with VBR 12 and is still accepted by 13
# (as deprecated), which makes it the widest default. Newer revisions add
# endpoints - the agent ones need 1.3.
DEFAULT_API_VERSION = "1.1-rev0"


class VeeamClient:
    """One connection to one Veeam Backup & Replication server.

    Read-only unless built with allow_changes=True; calls that start,
    stop or switch off work refuse to run otherwise.
    """

    def __init__(
        self,
        server_url: str,
        username: str,
        password: str,
        *,
        api_version: str = DEFAULT_API_VERSION,
        verify_tls: bool = True,
        ca_bundle: Path | None = None,
        page_size: int = 200,
        timeout: float = 120.0,
        allow_changes: bool = False,
    ) -> None:
        # The server's address, without a path: https://host:9419. Paths
        # (/api/v1/..., /api/oauth2/token) are added by the calls.
        self.server_url = server_url.rstrip("/")
        self.api_version = api_version
        self.page_size = page_size
        # Off by default. A caller that only reads should leave it off.
        self.allow_changes = allow_changes

        verify: bool | ssl.SSLContext = verify_tls
        if verify_tls and ca_bundle is not None:
            verify = ssl.create_default_context(cafile=str(ca_bundle))
        headers = {"x-api-version": api_version, "Accept": "application/json"}

        # Logging in and renewing go through their own plain client: they
        # cannot use the one whose authentication they are supplying.
        self._login_client = httpx.Client(verify=verify, headers=headers, timeout=timeout)
        self._auth = PasswordAuth(self.server_url, username, password, client=self._login_client)

        self._api = APIClient(
            base_url=self.server_url,
            auth=self._auth,
            default_headers=headers,
            timeout=timeout,
            transport=httpx.HTTPTransport(verify=verify),
        )
        # For calls that change something: one attempt, never a retry.
        self._api_once = APIClient(
            base_url=self.server_url,
            auth=self._auth,
            default_headers=headers,
            timeout=timeout,
            max_attempts=1,
            transport=httpx.HTTPTransport(verify=verify),
        )

        self.server = Server(self)
        self.sessions = Sessions(self)
        self.jobs = Jobs(self)
        self.backups = Backups(self)
        self.backup_objects = BackupObjects(self)
        self.restore_points = RestorePoints(self)
        self.repositories = Repositories(self)
        self.managed_servers = ManagedServers(self)
        self.agents = Agents(self)

    def close(self, *, logout: bool = True) -> None:
        """Close the connection, ending the session on the server first
        so its tokens stop being valid."""
        if logout:
            self._auth.logout()
        self._api.close()
        self._api_once.close()
        self._login_client.close()

    def __enter__(self) -> VeeamClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def api(self) -> APIClient:
        """The authenticated transport, for an endpoint the SDK does not
        wrap yet. Prefer adding it to the right group."""
        return self._api

    @property
    def api_once(self) -> APIClient:
        """The transport for changing calls: one attempt, no retry."""
        return self._api_once
