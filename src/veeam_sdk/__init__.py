"""A small SDK for the Veeam Backup & Replication REST API. See
client.VeeamClient and the README."""

from ._base import ChangesNotAllowed, VeeamError
from .client import DEFAULT_API_VERSION, VeeamClient
from .models import JobState, Session

__all__ = [
    "DEFAULT_API_VERSION",
    "ChangesNotAllowed",
    "JobState",
    "Session",
    "VeeamClient",
    "VeeamError",
]
