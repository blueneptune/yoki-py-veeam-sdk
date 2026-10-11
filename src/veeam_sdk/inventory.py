"""What exists and what is protected: backups, the objects in them,
their restore points, and the infrastructure around them.

In Veeam's terms: a *backup* is what a job writes to a repository; a
*backup object* is one protected thing inside it (a VM, a computer); a
*restore point* is one moment that object can be brought back to. "Is
this machine protected, and since when?" is answered by its newest
restore point.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from sdk_primer import NotFoundError

from ._base import Entities, Entity, Resource


class Backups(Resource):
    def list(self, *, name: str | None = None, job_id: str | None = None) -> Entities:
        """Backups (GET /api/v1/backups): id, name, jobId, platformName,
        creationTime, repositoryId."""
        return self._all("/api/v1/backups", {"nameFilter": name, "jobIdFilter": job_id})

    def get(self, backup_id: str) -> Entity | None:
        try:
            backup: Entity = self._get(f"/api/v1/backups/{backup_id}")
        except NotFoundError:
            return None
        return backup

    def objects(self, backup_id: str) -> Entities:
        """The protected objects inside one backup
        (GET /api/v1/backups/{id}/backupObjects)."""
        return self._all(f"/api/v1/backups/{backup_id}/backupObjects")


class BackupObjects(Resource):
    def iter_pages(
        self,
        *,
        name: str | None = None,
        platform: str | None = None,
        type: str | None = None,
    ) -> Iterator[Entities]:
        """Every protected object the server knows
        (GET /api/v1/backupObjects), a page at a time: id, name, type,
        platformName, objectId, restorePointsCount, viType, path."""
        yield from self._pages(
            "/api/v1/backupObjects",
            {"nameFilter": name, "platformNameFilter": platform, "typeFilter": type},
        )

    def list(self, *, name: str | None = None, platform: str | None = None) -> Entities:
        return [item for page in self.iter_pages(name=name, platform=platform) for item in page]

    def get(self, object_id: str) -> Entity | None:
        try:
            item: Entity = self._get(f"/api/v1/backupObjects/{object_id}")
        except NotFoundError:
            return None
        return item

    def restore_points(self, object_id: str, *, created_after: datetime | None = None) -> Entities:
        """One object's restore points
        (GET /api/v1/backupObjects/{id}/restorePoints), oldest first."""
        return self._all(
            f"/api/v1/backupObjects/{object_id}/restorePoints",
            {"createdAfterFilter": created_after, "orderColumn": "CreationTime", "orderAsc": True},
        )

    def latest_restore_point(self, object_id: str) -> Entity | None:
        """The newest restore point of one object - one request."""
        items, _ = self._page(
            f"/api/v1/backupObjects/{object_id}/restorePoints",
            {"orderColumn": "CreationTime", "orderAsc": False},
            0,
            1,
        )
        return items[0] if items else None


class RestorePoints(Resource):
    def iter_pages(
        self,
        *,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        backup_id: str | None = None,
        backup_object_id: str | None = None,
    ) -> Iterator[Entities]:
        """Restore points across everything (GET /api/v1/restorePoints),
        oldest first: id, name, platformName, creationTime, backupId,
        type (Full or Increment), malwareStatus."""
        yield from self._pages(
            "/api/v1/restorePoints",
            {
                "createdAfterFilter": created_after,
                "createdBeforeFilter": created_before,
                "backupIdFilter": backup_id,
                "backupObjectIdFilter": backup_object_id,
                "orderColumn": "CreationTime",
                "orderAsc": True,
            },
        )


class Repositories(Resource):
    def list(self) -> Entities:
        """Backup repositories (GET /api/v1/repositories)."""
        return self._all("/api/v1/repositories")

    def states(self) -> Entities:
        """Each repository's capacity and free space
        (GET /api/v1/repositories/states): capacityGB, freeGB, usedSpaceGB."""
        return self._all("/api/v1/repositories/states")


class ManagedServers(Resource):
    def list(self) -> Entities:
        """Servers added to the backup infrastructure - vCenters, hosts,
        Windows and Linux machines (GET /api/v1/managedServers)."""
        return self._all("/api/v1/managedServers")


class Agents(Resource):
    """Computers protected by Veeam Agent. These endpoints exist only in
    the newer API revisions (1.3, shipped with VBR 13); an older server,
    or an older `api_version`, answers 404."""

    def computers(self) -> Entities:
        """Discovered computers (GET /api/v1/agents/discoveredEntities)."""
        return self._all("/api/v1/agents/discoveredEntities")

    def protection_groups(self) -> Entities:
        return self._all("/api/v1/agents/protectionGroups")


class Server(Resource):
    def info(self) -> Entity:
        """The backup server's description of itself
        (GET /api/v1/serverInfo): name, buildVersion, vbrId, databaseVendor."""
        info: Entity = self._get("/api/v1/serverInfo")
        return info

    def time(self) -> Entity:
        """The backup server's clock (GET /api/v1/serverTime)."""
        clock: Entity = self._get("/api/v1/serverTime")
        return clock
