# yoki-py-veeam-sdk

Your Org's Keepsake Integrations - python - veeam - SDK


A small SDK for the **Veeam Backup & Replication** (VBR) REST API, built
on [sdk-primer-core](https://github.com/blueneptune/sdk-primer-core). It
is the Veeam counterpart of `yoki-py-commvault-sdk` and is laid out the
same way: one client object per backup server, a named group of calls
for each kind of thing, read-only unless told otherwise.

The repository is `yoki-py-veeam-sdk`; the package it provides is
`veeam-sdk`, imported as `veeam_sdk`.

> **Status: written from Veeam's documentation. No part of it has been
> run against a Veeam server.** Every call is covered by a test that
> fakes the server's answers, which proves the SDK does what the
> documentation describes - not that the documentation matches a real
> server. Expect a round of corrections on first contact; `veeam-probe`
> (below) exists to make that round short.

```python
from datetime import UTC, datetime, timedelta
from veeam_sdk import VeeamClient

with VeeamClient("https://vbr01.example.com:9419", r"LAB\svc_blt", password) as vbr:
    print(vbr.server.info()["buildVersion"])

    since = datetime.now(UTC) - timedelta(hours=24)
    for page in vbr.sessions.iter_pages(created_after=since, types=["BackupJob"]):
        for session in page:
            print(session.name, session.state, session.result, session.end_time)
```

## Veeam's words, next to Commvault's

| Veeam | What it is | Nearest Commvault idea |
|---|---|---|
| Backup server (VBR) | The thing this connects to | CommServe |
| Job | A definition: what to back up, where, when | Subclient with its plan |
| **Session** | One run of a job (or of a restore, a configuration backup, ...) | **Job** |
| Task session | One machine's part of a session | A job's per-client detail |
| Backup | What a job has written to a repository | - |
| Backup object | One protected thing: a VM, a computer | Client |
| Restore point | One moment an object can be restored to | A backup job's data for that client |
| Repository | Where backups are stored | Storage pool / library |

The one that matters most: **history is sessions.** What blt stores as a
"job" row for Commvault is a session here.

## The calls

All paths are under the server's address, `https://<host>:9419`.

| Call | Endpoint | Returns |
|---|---|---|
| `vbr.server.info()` | `GET /api/v1/serverInfo` | name, build, id |
| `vbr.server.time()` | `GET /api/v1/serverTime` | the server's clock |
| `vbr.sessions.iter_pages(...)` | `GET /api/v1/sessions` | pages of `Session`, oldest first |
| `vbr.sessions.sample(n, ...)` | same, one request | `(total, [Session])`, newest first |
| `vbr.sessions.oldest_start()` | same, one request | when history begins |
| `vbr.sessions.get(id)` | `GET /api/v1/sessions/{id}` | `Session`, or `None` if gone |
| `vbr.sessions.logs(id)` | `GET /api/v1/sessions/{id}/logs` | log lines |
| `vbr.sessions.task_sessions(id)` | `GET /api/v1/sessions/{id}/taskSessions` | per-machine results |
| `vbr.jobs.states()` | `GET /api/v1/jobs/states` | `JobState`: last run, last result, next run |
| `vbr.jobs.list()`, `.get(id)` | `GET /api/v1/jobs`, `/jobs/{id}` | job definitions |
| `vbr.backups.list()`, `.get(id)` | `GET /api/v1/backups`, `/backups/{id}` | backups |
| `vbr.backups.objects(id)` | `GET /api/v1/backups/{id}/backupObjects` | objects in one backup |
| `vbr.backup_objects.iter_pages()`, `.list()`, `.get(id)` | `GET /api/v1/backupObjects` | every protected object |
| `vbr.backup_objects.restore_points(id)` | `GET /api/v1/backupObjects/{id}/restorePoints` | one object's restore points |
| `vbr.backup_objects.latest_restore_point(id)` | same, one request | the newest one |
| `vbr.restore_points.iter_pages(...)` | `GET /api/v1/restorePoints` | restore points across everything |
| `vbr.repositories.list()`, `.states()` | `GET /api/v1/repositories`, `/repositories/states` | repositories, capacity and free space |
| `vbr.managed_servers.list()` | `GET /api/v1/managedServers` | vCenters, hosts, servers |
| `vbr.agents.computers()` | `GET /api/v1/agents/discoveredEntities` | agent-protected computers (API 1.3) |
| `vbr.agents.protection_groups()` | `GET /api/v1/agents/protectionGroups` | protection groups (API 1.3) |

**Changing calls** - refused unless the client was built with
`allow_changes=True`, and never retried:

| Call | Endpoint |
|---|---|
| `vbr.jobs.start(id)`, `.stop(id)`, `.retry(id)` | `POST /api/v1/jobs/{id}/start` etc. |
| `vbr.jobs.enable(id)`, `.disable(id)` | `POST /api/v1/jobs/{id}/enable` etc. |
| `vbr.sessions.stop(id)` | `POST /api/v1/sessions/{id}/stop` |

`Session` and `JobState` are typed and keep Veeam's own record in
`.raw`. Everything else comes back as Veeam's JSON, as dictionaries.
For an endpoint not wrapped yet, `vbr.api.get("/api/v1/...")` is the
authenticated transport.

## Session filters

`iter_pages` takes `created_after`, `created_before`, `ended_after`,
`ended_before` (timezone-aware datetimes; a naive one is refused rather
than guessed at), `types`, `state`, `results`, `job_id`, `name`.

- `types`: `BackupJob`, `ReplicaJob`, `ConfigurationBackup`,
  `RestoreVm`, `InstantRecovery`, `FileLevelRestore`, ... (thirty-odd).
- `state`: `Stopped`, `Starting`, `Working`, `Pausing`, `Resuming`,
  `WaitingTape`, `Idle`, `Postprocessing`. Anything but `Stopped` is
  still running (`session.is_active`).
- `results`: `None`, `Success`, `Warning`, `Failed`.

For a collector, that gives the same three questions blt asks Commvault:
what is running now (`state` other than Stopped), what has finished
since the last run (`ended_after`), and what finished in a past window
(`ended_after` + `ended_before`, for backfilling history).

## Logging in

VBR uses a user name and password, exchanged for a short-lived token:

- The client logs in on the first call, not when it is built.
- The access token lasts **15 minutes**. The client renews it a minute
  before it runs out, using the refresh token, and falls back to logging
  in again if the refresh token is no longer accepted. A `401` triggers
  one renewal and one retry.
- Closing the client (or leaving the `with` block) logs out, so the
  tokens stop being valid on the server.
- Tokens are kept in memory only. Nothing is written to disk.

The account needs a VBR role that can read; **Veeam Backup Viewer** is
the read-only one. A domain account is written `DOMAIN\user`.

## Options

| Argument | Default | |
|---|---|---|
| `api_version` | `1.1-rev0` | Sent as `x-api-version` on every request. See below. |
| `verify_tls` | `True` | VBR ships with a self-signed certificate; prefer `ca_bundle` to turning this off. |
| `ca_bundle` | - | A PEM file with the certificate authority (or the server's own certificate). |
| `page_size` | `200` | Items per request on listings. |
| `timeout` | `120` | Seconds. |
| `allow_changes` | `False` | Leave off for anything that only reads. |

**API versions.** Veeam's API is versioned by that header, and each VBR
release adds revisions: 1.1 came with VBR 12, 1.2 with 12.2 and later,
1.3 with 13. The default is the oldest, so the same client works on 12
and 13 - VBR 13 still accepts it, marked deprecated. Raise it
(`api_version="1.3-rev1"`) when every server is on 13; the agent calls
need it. An endpoint the chosen revision does not have answers 404.

## veeam-probe

Tries each read the SDK makes, once, and prints a line for each. It
changes nothing and stores nothing.

```
VEEAM_URL=https://vbr01.example.com:9419 \
VEEAM_USERNAME='LAB\svc_blt' VEEAM_PASSWORD='...' \
uv run veeam-probe
```
```
ok      server info                    vbr01 build 13.0.1.180
ok      session listing                212 sessions created in the last 24 h; types seen: BackupJob
FAILED  agent computers                NotFoundError: ...
```

Also read: `VEEAM_API_VERSION`, `VEEAM_VERIFY_TLS=false`,
`VEEAM_CA_BUNDLE`. **The first time this runs against a real server, its
output is what turns this README from expectation into fact.**

## What is not known

Things the documentation left open, or that a real server may answer
differently:

- **Everything, in the strict sense** - see the status note at the top.
- **Which databases a run covered.** Veeam protects SQL Server through
  application-aware processing inside a VM or agent backup. The API
  lists machines and their restore points; I found no listing of the
  databases inside a restore point short of starting a restore session.
  So "these databases exist, these were backed up" - which the Commvault
  SDK can answer - is not something this SDK can answer yet.
- **How a session's backup level is told** (full, incremental, synthetic
  full). It is not a field on the session; it is on the restore points
  (`type`) and possibly in task sessions.
- **Sizes and durations.** Sessions carry start and end times, not bytes
  transferred. Those are likely in task sessions, whose exact fields
  were not in the reference pages read.
- **Whether `stateFilter` takes one value or several**, and whether a
  "not Stopped" query needs one request per state.
- **How long sessions are kept** - VBR prunes session history after a
  configurable period (53 weeks by default), which bounds any backfill.
- **Refresh-token life.** The reference says 24 hours; a v13 field
  report says otherwise. The client does not depend on it.
- **Agent, tape and some other job types** are reported to have thinner
  REST coverage than VMware and Hyper-V jobs.
- **Rate limits.** None are documented.

Not covered at all: Veeam Service Provider Console, Veeam Backup for
Microsoft 365, and Enterprise Manager - each is a different API.

## Using it from another project

It is not on PyPI. Depend on a tag of this repository:

```toml
[project]
dependencies = ["veeam-sdk"]

[tool.uv.sources]
veeam-sdk = { git = "https://github.com/blueneptune/yoki-py-veeam-sdk.git", tag = "v0.1.0" }
```

## Development

```
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

The tests fake the server with `respx`; a request no test set up fails
the test instead of reaching the network.

## Releases

This SDK follows the same branch loop as blt (see the "Releases" section
of blt's README) with one difference: it is **not on the weekly clock**.
It is released whenever it has a change worth releasing, and blt takes
it by changing the tag it pins.

`main` holds only finished work. Nothing is committed to it directly.

| Thing | Name | Example |
|---|---|---|
| Feature branch | `F/<What_it_does>` | `F/Add_restore_calls` |
| Bug-fix branch | `B/<What_it_fixes>` | `B/Paging_stops_early` |
| Hotfix branch | `H/<What_it_fixes>` | `H/Token_renewal_loop` |
| Release tag, on `main` | `v<major>.<minor>.<patch>` | `v0.2.0` |

Versions here are ordinary `major.minor.patch`, not blt's weekly names:
a new call or option is a minor step (`v0.1.0` to `v0.2.0`), a fix is a
patch step (`v0.2.0` to `v0.2.1`), and a change that would break a
caller is a major step. The number in `pyproject.toml` and the tag
always match.

### One piece of work

```
git checkout main && git pull
git checkout -b F/Add_restore_calls
# work in small commits; run the checks often (see Development)
git push -u origin F/Add_restore_calls
```

### Releasing

Once the branch's checks pass:

```
git checkout main && git pull
git merge --no-ff F/Add_restore_calls
# set the new version in pyproject.toml, then:
uv lock
git commit -am "Release v0.2.0"
git tag -a v0.2.0 -m "What changed, in a line or two"
git push origin main v0.2.0
```

The tag has to be pushed by name, as above: a plain `git push` does not
send tags, and blt cannot pin a tag that is not on GitHub.

Then, in blt, on that week's release branch: change the tag for
`veeam-sdk` under `[tool.uv.sources]` in `pyproject.toml`, run `uv lock`,
run blt's tests, commit.

### Tidying up

A branch is deleted once it has been merged, locally and on GitHub:

```
git branch -d F/Add_restore_calls
git push origin --delete F/Add_restore_calls
```

`git branch -d` refuses to delete a branch that has not been merged,
which is the check that nothing is lost.

## Sources

- [VBR REST API reference (v13, 1.3-rev1)](https://helpcenter.veeam.com/references/vbr/13/rest/1.3-rev1/tag/SectionOverview) - authentication, versions, paging, every endpoint above
- [Sessions](https://helpcenter.veeam.com/references/vbr/13/rest/1.3-rev1/tag/Sessions) and [Backup Objects](https://helpcenter.veeam.com/references/vbr/13/rest/1.3-rev1/tag/Backup-Objects) sections of the same
- [Veeam community: v13 REST API automation](https://community.veeam.com/blogs-and-podcasts-57/veeam-v13-rest-api-automation-authentication-job-orchestration-and-monitoring-13002) - field notes: the mandatory version header, refresh-token behaviour, coverage gaps
