"""The preview record: what apply will write, decided before anyone confirms it.

A preview used to be a *fingerprint* — a hash of everything that must not change
between the dry-run and the apply — while the content itself stayed in the
agent's conversation and was handed back at apply time. That makes the guarantee
backwards: the tool could prove the world had not moved, but not that the bytes
it wrote were the bytes the user saw. Anything that re-renders between the two
(a re-run subagent, a lost message, a different model) produces a plausible
worklog nobody approved, and every check still passes.

So the record here is the artifact, not a receipt for one (roadmap §10.1). It
carries the complete final text of every file the apply will write, and
``apply`` writes *that* — it takes a preview id and nothing else. There is no
input at apply time for a re-render to come in through.

What is stored, and why each part is here:

* **The payload** — every day file's and index.md's final text, plus their
  hashes. This is the whole point.
* **Repository fingerprint** — identity, git dir, branch, HEAD, submodules and
  (only when the run read it) the working tree. Answers "is this the same
  repository, in the same state, as the one that was analysed?"
* **Worklog fingerprint** — index.md's hash, each target day file's hash, and
  the day-file listing. Answers "has anyone edited the worklog since?" An added
  or removed day file counts: the index is a function of the whole directory.
* **Run fingerprint** — the hashes of the analysis run's manifests and results.
  Answers "is this still the analysis that produced this payload?"
* **Config fingerprint** — the project's language settings. They are read at
  preview time to decide what gets rendered, so a change to them between preview
  and apply means the stored payload is no longer what the project asked for.
* **TTL and state** — below.

The state machine (roadmap §17, PR 6)::

    previewed ──(apply: verified)──▶ confirmed ──(write lands)──▶ applied
        │                               │
        │                               └──(write failed, rolled back)──▶ failed
        ├──(cancel)──▶ cancelled
        ├──(TTL passes)──▶ expired
        └──(the world moved)──▶ stale

``expired`` and ``stale`` are computed, never stored: they are facts about the
world now, not decisions the tool made, and reading a record must not rewrite
it. ``confirmed`` *is* stored, and stored before the write starts — a record
still sitting in ``confirmed`` means a process died mid-apply, which is the one
state worth refusing to guess about.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import socket
from datetime import datetime, timedelta, timezone

from git_worklog import __version__, config, paths, writer
from git_worklog import markers as wm
from git_worklog.analysis import RESULTS_SUBDIR, TASKS_SUBDIR
from git_worklog.analysis import SCHEMA_VERSION as ANALYSIS_SCHEMA_VERSION
from git_worklog.analysis import history as ah
from git_worklog.analysis import manifest as am
from git_worklog.analysis import results as ar
from git_worklog.analysis import worktree as aw

SCHEMA_VERSION = 1
DEFAULT_TTL_SECONDS = 24 * 3600

# Stored states.
PREVIEWED = "previewed"
CONFIRMED = "confirmed"
APPLIED = "applied"
CANCELLED = "cancelled"
FAILED = "failed"
# Computed states -- see the module docstring.
EXPIRED = "expired"
STALE = "stale"

#: States from which nothing further happens.
TERMINAL = {APPLIED, CANCELLED, FAILED}

MISSING = "missing"


class PreviewError(ValueError):
    """A refused preview or apply, carrying the wire code its caller reports."""

    def __init__(self, code: str, message: str, **extra):
        self.code = code
        self.message = message
        self.extra = extra
        super().__init__(message)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def now_utc(override: "str | None" = None) -> datetime:
    """Current time, or a caller-supplied one so tests are not clock-dependent."""
    if override:
        dt = datetime.fromisoformat(override)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Fingerprints — the three worlds a preview depends on
# --------------------------------------------------------------------------

def repository_fingerprint(repo: str, include_uncommitted: bool) -> dict:
    """Everything about the repository the payload depends on."""
    info = ah.repo_info(repo)
    return {
        "identity": ah.identity(repo),
        "root": info["root"],
        "git_dir": info["git_dir"],
        "branch": info["branch"],
        "head": info["head"],
        "submodule_fingerprint": ah.submodule_fingerprint(repo),
        # Only when the run actually read the working tree. A run that analysed
        # commits alone cannot have its output changed by an unrelated edit, and
        # invalidating its preview because someone saved a file would be a
        # refusal with nothing behind it. This is narrower than a literal reading
        # of §10.3's "Working Tree", and deliberately so.
        "worktree_fingerprint": (aw.inspect(repo)["worktree_fingerprint"]
                                 if include_uncommitted else None),
    }


def worklog_fingerprint(worklog_dir: str, dates: "list[str]") -> dict:
    """The worklog directory as it stands, in the three ways it can drift.

    Per-date hashes catch an edit to a day this preview would overwrite;
    ``index_sha256`` catches an edit to the index; ``dir_fingerprint`` catches a
    day file appearing or vanishing, which changes the index without touching
    anything this preview names.
    """
    layout = wm.detect_layout(worklog_dir)
    day_files = {}
    for date in dates:
        path = wm.day_path(worklog_dir, date, layout)
        day_files[date] = _file_sha256(path)
    listing = "\x00".join(sorted(wm.list_day_dates(worklog_dir, layout)))
    return {
        "index_sha256": _file_sha256(wm.index_path(worklog_dir)),
        "day_files": day_files,
        "dir_fingerprint": _sha256(listing),
    }


def _file_sha256(path: str) -> str:
    """The file's hash, or ``"missing"``. Absent and empty must not collide."""
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return MISSING


def _dir_fingerprint(directory: str) -> str:
    """Hash every ``.json`` in ``directory``, by name and by content."""
    h = hashlib.sha256()
    try:
        names = sorted(n for n in os.listdir(directory) if n.endswith(".json"))
    except OSError:
        return MISSING
    for name in names:
        h.update(name.encode("utf-8"))
        h.update(b"\x00")
        try:
            with open(os.path.join(directory, name), "rb") as fh:
                h.update(fh.read())
        except OSError:
            h.update(b"<unreadable>")
        h.update(b"\x00")
    return h.hexdigest()


def run_fingerprint(run_dir: str) -> dict:
    """The analysis run's manifests and results, hashed separately.

    Separately because the two say different things when they move: changed
    results mean someone edited the analysis this payload was written from;
    changed tasks mean the run was re-prepared, possibly asking for a different
    language than the one the payload is written in (§6.2.10).
    """
    return {
        "tasks_fingerprint": _dir_fingerprint(os.path.join(run_dir, TASKS_SUBDIR)),
        "results_fingerprint": _dir_fingerprint(os.path.join(run_dir, RESULTS_SUBDIR)),
    }


def config_fingerprint(worklog_dir: str) -> dict:
    """The project settings that decide what a preview renders.

    Both are read at preview time -- the content language feeds the run, the
    index language decides index.md's headings -- so a change to either between
    preview and apply means the stored bytes are no longer what the project is
    asking for. Recomputing the payload instead would apply something nobody
    saw, which is the failure this whole module exists to prevent.
    """
    data = config.load(worklog_dir)
    return {"language": config.language(data),
            "index_language": config.index_language(data)}


# --------------------------------------------------------------------------
# Building the record
# --------------------------------------------------------------------------

def build(run_dir: str, entries: dict, repo: str = ".",
          worklog_dir: "str | None" = None,
          ttl_seconds: "int | None" = None,
          now: "datetime | None" = None) -> dict:
    """Turn a collected run plus the day's rendered prose into a preview record.

    ``entries`` is ``{date: {"generated_markdown": ...}}`` — the one thing the
    CLI cannot produce, because writing it means reading patches and choosing
    words (§6.1). It comes in here, once, before the user is shown anything, and
    is never asked for again.
    """
    now = now or now_utc()
    ttl_seconds = DEFAULT_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    if not isinstance(entries, dict) or not entries:
        raise PreviewError("NO_ENTRIES",
                           "No entries provided, so there is nothing to preview.")

    tasks = am.load_tasks(run_dir)
    worklog_dir = worklog_dir or os.path.join(repo, wm.WORKLOG_DIRNAME)
    writer.check_layout(worklog_dir)

    # A day the run never dispatched has not been analysed, language-checked or
    # evidence-checked. Writing it would put content into the worklog that no
    # part of the pipeline ever looked at -- the same failure `collect` reports
    # as `unknown`, arriving one step later.
    unknown = sorted(set(entries) - set(tasks["dates"]))
    if unknown:
        raise PreviewError(
            "UNKNOWN_DATE",
            f"Run {os.path.basename(run_dir)} never analysed "
            f"{', '.join(unknown)}, so there is no validated result behind "
            f"{'them' if len(unknown) > 1 else 'it'}. Prepare a run that covers "
            f"the dates you want to write.",
            dates=unknown, run_dir=run_dir)

    # Re-run collect's verdict rather than trust that it was run. `previewed` is
    # only reachable from `collected`, and a preview that took the caller's word
    # for it would be exactly the conversation-dependence this record removes.
    collected = ar.read_run(tasks["results_dir"], tasks["dates"], repo,
                            tasks["language"], tasks["required_by_date"])
    if collected["partial_run"]:
        raise PreviewError(
            "RUN_NOT_COLLECTED",
            "This run is partial — some days are missing, invalid, degraded, or "
            "disagree on language — so it cannot go to preview. Fix or re-run "
            "those days first.",
            run_dir=run_dir,
            missing=collected["missing"], degraded=collected["degraded"],
            invalid=[i["date"] for i in collected["invalid"]],
            languages_seen=collected["languages_seen"])

    info = ah.repo_info(repo)
    meta = {"timezone": tasks["manifests"][tasks["dates"][0]].get("timezone"),
            "branch": info["branch"], "head": info["short_head"]}
    days = writer.plan_days(worklog_dir, entries, meta)
    index = writer.plan_index(worklog_dir,
                              {d["date"]: d["summary"] for d in days},
                              tasks["language"])

    include_uncommitted = _include_uncommitted(tasks)
    dates = sorted(entries)
    fingerprint_dates = sorted(set(tasks["dates"]) | set(dates))
    preview_id = _mint_id(now, days, index)
    record = {
        "schema_version": SCHEMA_VERSION,
        "preview_id": preview_id,
        "state": PREVIEWED,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
        "ttl_seconds": ttl_seconds,
        "confirmed_at": None,
        "applied_at": None,
        "cancelled_at": None,
        "failure": None,
        "versions": {
            "cli": __version__,
            "layout": wm.LAYOUT_VERSION,
            "preview_schema": SCHEMA_VERSION,
            "analysis_schema": ANALYSIS_SCHEMA_VERSION,
        },
        "run": {
            "run_id": os.path.basename(run_dir.rstrip(os.sep)),
            "run_dir": run_dir,
            "dates": tasks["dates"],
            **run_fingerprint(run_dir),
        },
        "repository": repository_fingerprint(repo, include_uncommitted),
        "worklog": worklog_fingerprint(worklog_dir, fingerprint_dates),
        "params": {
            "worklog_dir": os.path.abspath(worklog_dir),
            "timezone": meta["timezone"],
            "include_uncommitted": include_uncommitted,
            "fingerprint_dates": fingerprint_dates,
        },
        "language": {
            "resolved": tasks["language"],
            "source": _language_source(tasks),
        },
        "config": config_fingerprint(worklog_dir),
        "payload": {
            "days": [{
                "date": d["date"],
                "path": d["path"],
                "action": d["action"],
                "manual_preserved": d["manual_preserved"],
                "summary": d["summary"],
                "content": d["content"],
                "content_sha256": _sha256(d["content"]),
                "original_sha256": (_sha256(d["original"])
                                    if d["original"] is not None else None),
            } for d in days],
            "index": {
                "path": index["index_path"],
                "action": index["action"],
                "dates": index["dates"],
                "preserved_manual": index["preserved_index_manual"],
                "language": index["index_language"],
                "language_source": index["index_language_source"],
                "content": index["content"],
                "content_sha256": _sha256(index["content"]),
                "original_sha256": (_sha256(index["original"])
                                    if index["original"] is not None else None),
            },
        },
        # Days the run analysed but that this preview will not write. Usually
        # correct -- a day with no changes gets no file (§6 of worklog-format) --
        # but silently dropping a day the user expected is not something to find
        # out after apply, so the number is put in front of them.
        "not_written": sorted(set(tasks["dates"]) - set(dates)),
        "warnings": index["warnings"],
    }
    return record


def _include_uncommitted(tasks: dict) -> bool:
    return any(m.get("include_uncommitted") for m in tasks["manifests"].values())


def _language_source(tasks: dict) -> "str | None":
    first = tasks["manifests"][tasks["dates"][0]]
    return (first.get("language") or {}).get("source")


def _mint_id(now: datetime, days: "list[dict]", index: dict) -> str:
    """Name the record after its payload *and* its moment.

    The payload alone would be tempting -- identical bytes, identical id -- but
    two previews of the same content would then collide on one file, and the
    second would silently overwrite the first's state. An already-applied record
    losing its "applied" that way is exactly the double-write the state machine
    is there to stop.
    """
    basis = hashlib.sha256()
    for d in days:
        basis.update(f"{d['date']}\x00{d['content']}\x00".encode("utf-8"))
    basis.update(index["content"].encode("utf-8"))
    basis.update(now.isoformat().encode("utf-8"))
    return f"rw-{now.strftime('%Y%m%d')}-{basis.hexdigest()[:6]}"


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

def preview_path(preview_id: str) -> str:
    return os.path.join(paths.previews_dir(), f"{preview_id}.json")


def save(record: dict) -> str:
    """Write the record owner-only, atomically. Returns its path."""
    paths.ensure_dir(paths.previews_dir())
    path = preview_path(record["preview_id"])
    tmp = f"{path}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


def load(preview_id: str) -> dict:
    path = preview_path(preview_id)
    if not os.path.exists(path):
        raise PreviewError("UNKNOWN_PREVIEW",
                           f"No preview found for id {preview_id!r}.",
                           preview_id=preview_id)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            record = json.load(fh)
    except (OSError, ValueError) as exc:
        raise PreviewError("PREVIEW_UNREADABLE",
                           f"Preview {preview_id} could not be read: {exc}",
                           preview_id=preview_id)
    stored = record.get("schema_version")
    if stored != SCHEMA_VERSION:
        # A record from another version of this schema may not mean what this
        # code would read it to mean, and a preview is not worth guessing about:
        # rebuilding one is cheap and applying a misread one is not.
        raise PreviewError(
            "PREVIEW_SCHEMA_MISMATCH",
            f"Preview {preview_id} uses schema version {stored}, but this CLI "
            f"reads version {SCHEMA_VERSION}. Build a fresh preview.",
            preview_id=preview_id, found=stored, expected=SCHEMA_VERSION)
    return record


def public(record: dict) -> dict:
    """The record without the payload text — what a caller wants to look at.

    The file contents are the bulk of a record and are already on the user's
    screen from the dry-run. Repeating them in every ``show`` turns a status
    check into a wall of Markdown.
    """
    out = {k: v for k, v in record.items() if k != "payload"}
    payload = record.get("payload") or {}
    out["files"] = [
        {"path": d["path"], "action": d["action"], "date": d["date"],
         "sha256": d["content_sha256"]}
        for d in payload.get("days", [])
    ]
    index = payload.get("index")
    if index:
        out["files"].append({"path": index["path"], "action": index["action"],
                             "sha256": index["content_sha256"]})
    return out


# --------------------------------------------------------------------------
# The state machine
# --------------------------------------------------------------------------

# Compared between build-time and apply-time, with a label a human can act on.
# Each entry is a way the world can move underneath a stored payload; nothing
# the caller re-supplies is listed, because apply takes no input to re-supply it
# with -- that is the difference this record makes.
_CONSISTENCY_KEYS = [
    ("repository", "identity", "repository identity"),
    ("repository", "root", "repository"),
    ("repository", "git_dir", "git directory"),
    ("repository", "branch", "branch"),
    ("repository", "head", "HEAD"),
    ("repository", "submodule_fingerprint", "submodules"),
    ("repository", "worktree_fingerprint", "working tree"),
    ("worklog", "index_sha256", "index.md content"),
    ("worklog", "day_files", "day files"),
    ("worklog", "dir_fingerprint", "worklog directory listing"),
    ("run", "tasks_fingerprint", "analysis tasks"),
    ("run", "results_fingerprint", "analysis results"),
    # §6.2.10: a project that changed its language between preview and apply is
    # asking for a different worklog, not the same one rendered differently.
    ("config", "language", "project language setting"),
    ("config", "index_language", "project index language setting"),
]


def snapshot(record: dict) -> dict:
    """Re-read the world, using only what the record itself says to look at.

    Apply passes a preview id and nothing else, so the record has to be enough
    to find everything it depends on. That is the property being relied on here.
    """
    params = record["params"]
    repo = record["repository"]["root"]
    worklog_dir = params["worklog_dir"]
    return {
        "repository": repository_fingerprint(repo, params["include_uncommitted"]),
        "worklog": worklog_fingerprint(worklog_dir, params["fingerprint_dates"]),
        "run": run_fingerprint(record["run"]["run_dir"]),
        "config": config_fingerprint(worklog_dir),
    }


def _nested(data: dict, group: str, key: str):
    return (data.get(group) or {}).get(key)


def evaluate(record: dict, current: "dict | None" = None,
             now: "datetime | None" = None) -> dict:
    """The record's state right now, and why.

    ``current`` is a :func:`snapshot`; omit it to check only the things that need
    no look at the world (TTL and the stored state), which is what ``show``
    wants and what a caller with no repository access can still answer.
    """
    now = now or now_utc()
    stored = record.get("state", PREVIEWED)
    expires = datetime.fromisoformat(record["expires_at"])
    expired = now > expires

    mismatches = []
    if current is not None:
        for group, key, label in _CONSISTENCY_KEYS:
            expected = _nested(record, group, key)
            actual = _nested(current, group, key)
            if expected != actual:
                mismatches.append({"field": label, "expected": expected,
                                   "actual": actual})

    # A decision the tool already recorded outranks anything about the world:
    # an applied preview is applied whether or not it also expired since.
    if stored in TERMINAL or stored == CONFIRMED:
        state = stored
    elif expired:
        state = EXPIRED
    elif mismatches:
        state = STALE
    else:
        state = PREVIEWED

    return {
        "preview_id": record["preview_id"],
        "state": state,
        "stored_state": stored,
        "applicable": state == PREVIEWED,
        "mismatches": mismatches,
        "expired": expired,
        "expires_at": record["expires_at"],
        "age_seconds": int((now - datetime.fromisoformat(record["created_at"]))
                           .total_seconds()),
        "reason": _reason(state, mismatches),
    }


_REASONS = {
    PREVIEWED: None,
    APPLIED: "This preview has already been applied.",
    CANCELLED: "This preview was cancelled.",
    FAILED: "A previous apply of this preview failed and was rolled back. "
            "Build a fresh preview rather than retrying this one.",
    CONFIRMED: "A previous apply of this preview was interrupted after it was "
               "confirmed, so whether it wrote anything is unknown. Check the "
               "worklog directory, then build a fresh preview.",
    EXPIRED: "This preview has expired.",
}


def _reason(state: str, mismatches: "list[dict]") -> "str | None":
    if state == STALE:
        fields = ", ".join(m["field"] for m in mismatches)
        return f"The state changed since the preview was built: {fields}."
    return _REASONS.get(state)


def cancel(record: dict, now: "datetime | None" = None) -> dict:
    """Retire a preview the user decided against."""
    now = now or now_utc()
    stored = record.get("state", PREVIEWED)
    if stored in TERMINAL:
        raise PreviewError("PREVIEW_NOT_OPEN",
                           f"Preview {record['preview_id']} is already {stored}; "
                           f"there is nothing to cancel.",
                           preview_id=record["preview_id"], state=stored)
    record["state"] = CANCELLED
    record["cancelled_at"] = now.isoformat()
    save(record)
    return record


# --------------------------------------------------------------------------
# Concurrent apply lock
# --------------------------------------------------------------------------

class ApplyLock:
    """One writer at a time per worklog directory.

    Scoped to the directory rather than to the preview because that is where the
    damage would be: two *different* previews applying at once would interleave
    their day-file transactions and rebuild the index from a directory that is
    halfway through someone else's write. Two applies of the *same* preview are
    caught by the state machine, but only if they do not race it.

    The lock lives under ``~/.git-worklog/tmp/`` -- never in the repository,
    which is project content. A lock left behind by a process that died is
    broken only when it is provably dead: same host, and a pid that is gone.
    Across hosts there is nothing to check, so it is left alone and reported.
    """

    def __init__(self, worklog_dir: str, now: "datetime | None" = None):
        key = hashlib.sha256(
            os.path.realpath(worklog_dir).encode("utf-8")).hexdigest()[:16]
        self.worklog_dir = worklog_dir
        self.path = os.path.join(paths.tmp_dir(), f"apply-{key}.lock")
        self._now = now or now_utc()
        self.broke_stale = False

    def __enter__(self) -> "ApplyLock":
        paths.ensure_dir(paths.tmp_dir())
        try:
            self._create()
        except FileExistsError:
            holder = self._read()
            if not _holder_alive(holder):
                self.broke_stale = True
                os.unlink(self.path)
                self._create()
            else:
                raise PreviewError(
                    "APPLY_LOCKED",
                    f"Another apply is writing to {self.worklog_dir} "
                    f"(pid {holder.get('pid')} on {holder.get('host')}, since "
                    f"{holder.get('acquired_at')}). Wait for it to finish.",
                    lock_path=self.path, holder=holder)
        return self

    def _create(self) -> None:
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "host": socket.gethostname(),
                       "acquired_at": self._now.isoformat(),
                       "worklog_dir": self.worklog_dir}, fh)

    def _read(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            # An unreadable lock names no holder, so it can never be proven
            # dead and will never be broken. That is the safe direction: it
            # blocks writes rather than allowing a racing one.
            return {}

    def __exit__(self, *exc) -> None:
        try:
            os.unlink(self.path)
        except OSError:
            pass


def _holder_alive(holder: dict) -> bool:
    """Whether the lock's owner still exists. Unknown counts as alive."""
    pid, host = holder.get("pid"), holder.get("host")
    if not isinstance(pid, int) or host != socket.gethostname():
        return True
    try:
        os.kill(pid, 0)
    except OSError as exc:
        # ESRCH: no such process. EPERM: it exists and is someone else's.
        return exc.errno != errno.ESRCH
    return True


# --------------------------------------------------------------------------
# Apply
# --------------------------------------------------------------------------

def apply(record: dict, now: "datetime | None" = None) -> dict:
    """Write the stored payload, or refuse. Nothing is re-rendered here.

    The record moves to ``confirmed`` on disk *before* the first byte is written,
    so a crash mid-apply leaves evidence that a write was in flight rather than a
    record that still claims nothing happened.
    """
    now = now or now_utc()
    verdict = evaluate(record, snapshot(record), now)
    if not verdict["applicable"]:
        # The field list only belongs to a stale verdict. An applied preview
        # also "mismatches" -- against the files it wrote itself -- and printing
        # that next to "already applied" reads as though somebody else had been
        # editing, sending the reader to look for a problem that is not there.
        extra = ({"mismatches": verdict["mismatches"]}
                 if verdict["state"] == STALE else {})
        raise PreviewError(
            _refusal_code(verdict["state"]), verdict["reason"],
            preview_id=record["preview_id"], state=verdict["state"],
            instruction="Build a fresh preview before applying.", **extra)

    params = record["params"]
    payload = record["payload"]
    with ApplyLock(params["worklog_dir"], now) as lock:
        record["state"] = CONFIRMED
        record["confirmed_at"] = now.isoformat()
        save(record)

        # writer.apply_days wants the shape plan_days returns, and `original`
        # with it -- it is what a rollback restores. Reading it from disk now is
        # sound precisely because the fingerprint check above just proved every
        # target file is byte-identical to what the preview recorded.
        writes = [{**d, "original": _current_text(d["path"])}
                  for d in payload["days"]]
        try:
            writer.apply_days(params["worklog_dir"], writes, params["timezone"])
        except Exception as exc:  # noqa: BLE001 — any write failure ends the same way
            record["state"] = FAILED
            record["failure"] = f"{type(exc).__name__}: {exc}"
            save(record)
            raise PreviewError(
                "WRITE_FAILED",
                f"Transactional write failed and was rolled back: {exc}",
                preview_id=record["preview_id"],
                worklog_dir=params["worklog_dir"])

        # The day files are the worklog; index.md is navigation derived from
        # them. Once the day transaction lands, the preview *has* been applied,
        # and the index failing afterwards is a repairable inconsistency rather
        # than a reason to claim nothing happened -- or to tear the day files
        # back out (worklog-format.md §7). So the state is recorded first and
        # the index failure is reported on top of it, with the repair.
        record["state"] = APPLIED
        record["applied_at"] = now.isoformat()
        save(record)

        try:
            writer.apply_index(payload["index"]["path"], payload["index"]["content"])
        except Exception as exc:  # noqa: BLE001
            record["failure"] = f"index write failed: {type(exc).__name__}: {exc}"
            save(record)
            raise PreviewError(
                "INDEX_WRITE_FAILED",
                f"The day files were written, but index.md was not: {exc}. No "
                f"day data is lost — the index is rebuilt from the day files, so "
                f"run `rebuild_worklog_index.py --dir {params['worklog_dir']} "
                f"--apply` to repair it.",
                preview_id=record["preview_id"],
                worklog_dir=params["worklog_dir"], state=APPLIED)

    written = [d["date"] for d in payload["days"] if d["action"] != "no_change"]
    return {
        "preview_id": record["preview_id"],
        "state": APPLIED,
        "worklog_dir": params["worklog_dir"],
        "written_dates": written,
        "index_action": payload["index"]["action"],
        "index_path": payload["index"]["path"],
        "preserved_manual_dates": sorted(d["date"] for d in payload["days"]
                                         if d["action"] == "overwrite"
                                         and d["manual_preserved"]),
        "broke_stale_lock": lock.broke_stale,
    }


def _current_text(path: str) -> "str | None":
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def _refusal_code(state: str) -> str:
    return {STALE: "PREVIEW_STALE", EXPIRED: "PREVIEW_EXPIRED",
            APPLIED: "PREVIEW_ALREADY_APPLIED", CANCELLED: "PREVIEW_CANCELLED",
            FAILED: "PREVIEW_FAILED",
            CONFIRMED: "PREVIEW_INTERRUPTED"}.get(state, "PREVIEW_NOT_APPLICABLE")
