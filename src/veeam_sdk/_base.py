"""What every group of endpoints shares: how a call is made, how Veeam's
paged listings are walked, and the rule about changes.

Two kinds of call, treated differently:

- `_get` / `_pages`  - reads. Retried on a timeout or a gateway error.
- `_change`          - anything that alters the backup server (starting
                       or stopping a job, stopping a session, ...).
                       Refused unless the client was built with
                       allow_changes=True, and sent exactly once: if
                       "start this job" times out the server may well
                       have started it, and sending it again starts it
                       twice.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx
from sdk_primer import SDKError, ServerError

if TYPE_CHECKING:
    from .client import VeeamClient

# Veeam's own JSON for one thing, and a list of them. Named because
# several groups have a method called `list`, which would otherwise
# shadow the built-in in their own annotations.
Entity = dict[str, Any]
Entities = list[Entity]


class VeeamError(SDKError):
    """Veeam answered, and the answer was not usable."""


class ChangesNotAllowed(VeeamError):
    """A call that would alter the backup server was made on a client
    that was not built with allow_changes=True."""


def json_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        raise ServerError(
            f"{response.request.method} {response.request.url} returned "
            f"{response.headers.get('content-type', 'no content type')} instead of JSON."
        ) from None


def as_filter(value: datetime) -> str:
    """A time, the way Veeam's *Filter query parameters take it."""
    if value.tzinfo is None:
        raise ValueError("Filter times must be timezone-aware.")
    return value.isoformat()


def clean(params: dict[str, Any]) -> dict[str, Any]:
    """Drop what was not asked for, and spell times and booleans the way
    the API reads them. Lists stay lists: httpx repeats the key, which is
    how Veeam takes its array filters."""
    out: dict[str, Any] = {}
    for key, value in params.items():
        if value is None or value == []:
            continue
        if isinstance(value, datetime):
            value = as_filter(value)
        elif isinstance(value, bool):
            value = "true" if value else "false"
        out[key] = value
    return out


class Resource:
    """One group of related endpoints, reached as an attribute of
    VeeamClient."""

    def __init__(self, client: VeeamClient) -> None:
        self._client = client

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return json_body(self._client.api.get(path, params=clean(params or {})))

    def _page(
        self, path: str, params: dict[str, Any] | None, skip: int, limit: int
    ) -> tuple[Entities, int | None]:
        """One page of a listing: (items, the server's total if it gave one).

        Veeam wraps listings as {"data": [...], "pagination": {"total",
        "count", "skip", "limit"}}."""
        body = self._get(path, {**(params or {}), "skip": skip, "limit": limit})
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            raise VeeamError(f"GET {path} did not return a paged listing: {str(body)[:200]}")
        total = (body.get("pagination") or {}).get("total")
        return body["data"], total

    def _pages(
        self, path: str, params: dict[str, Any] | None = None, page_size: int | None = None
    ) -> Iterator[Entities]:
        """Every page of a listing. Stops on a short page, so it does not
        depend on the server reporting a total."""
        size = page_size or self._client.page_size
        skip = 0
        while True:
            items, _ = self._page(path, params, skip, size)
            if items:
                yield items
            if len(items) < size:
                return
            skip += size

    def _all(self, path: str, params: dict[str, Any] | None = None) -> Entities:
        return [item for page in self._pages(path, params) for item in page]

    def _change(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        if not self._client.allow_changes:
            raise ChangesNotAllowed(
                f"{method} {path} would change the backup server; this client is read-only. "
                "Build it with allow_changes=True if that is really intended."
            )
        # One attempt only - see the module docstring.
        response = self._client.api_once.request(method, path, json=body)
        return json_body(response) if response.content else None
