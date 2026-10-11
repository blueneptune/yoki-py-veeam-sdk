from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
import respx

SERVER = "https://vbr.test:9419"
TOKEN = {
    "access_token": "access-1",
    "token_type": "bearer",
    "refresh_token": "refresh-1",
    "expires_in": 900,
}


def page(items: list[dict[str, Any]], total: int | None = None) -> dict[str, Any]:
    return {
        "data": items,
        "pagination": {"total": len(items) if total is None else total, "count": len(items)},
    }


@pytest.fixture
def vbr_api() -> Iterator[respx.MockRouter]:
    """Every request is intercepted; one nobody mocked fails the test
    rather than reaching the network. Login and logout are in place."""
    with respx.mock(base_url=SERVER, assert_all_called=False) as router:
        router.post("/api/oauth2/token").respond(json=TOKEN)
        router.post("/api/oauth2/logout").respond(200)
        yield router
