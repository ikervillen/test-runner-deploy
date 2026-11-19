"""Lane-owned durable intake: select Fenix names before reading file contents."""

import hashlib
import json
import os
import sqlite3
import time
from collections import defaultdict, deque
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from .common import CaptureError, IntegrityError, canonical, digest, now
from .pipeline_policy import LANES
from .sources import Snapshot, capture, capture_identity, export_kind, parse_name, recover_capture


@dataclass(frozen=True)
class Candidate:
    origin: str
    path: Path
    family: str
    producer_time: str | None
    audit: bool = True

    @property
    def clock(self):
        return datetime.fromisoformat(self.producer_time) if self.producer_time else None

    @property
    def order(self):
        return self.origin, self.path.name


def classify_name(origin, folder, name):
    family = export_kind(name)
    if not family:
        return None, None
    path = folder / name
    try:
        return Candidate(origin, path, family, parse_name(name, family)), None
    except CaptureError as exc:
        return None, {"origin": origin, "path": str(path), "error": str(exc)}


def discover_lanes(sources, policy, *, cache=None):
    """Only directory names are read here; historical files are not statted/opened."""
    candidates, errors = [], []
    for origin, folder in sources:
        try:
            with os.scandir(folder) as entries:
                names = sorted(e.name for e in entries if e.name.lower().endswith(".csv"))
        except OSError as exc:
            errors.append({"origin": origin, "path": str(folder), "error": str(exc)})
            continue
        previous = {} if cache is None else cache.get((origin, folder), {})
        current = {}
        for name in names:
            candidate, error = (
                previous[name] if name in previous else classify_name(origin, folder, name)
            )
            if candidate:
                candidates.append(candidate)
            if error:
                errors.append(error)
            if cache is not None:
                current[name] = candidate, error
        if cache is not None:
            cache[origin, folder] = current
    timed = [c.clock for c in candidates if c.producer_time and "T" in c.producer_time]
    window = policy.source_window(max(timed)) if timed else None
    daily = {}
    for item in candidates:
        if item.producer_time and "T" not in item.producer_time:
            key = item.origin, item.family
            daily[key] = max(daily.get(key, item.producer_time), item.producer_time)
    live, history = [], []
    for item in candidates:
        if item.producer_time is None:
            live.append(item)
        elif "T" not in item.producer_time:
            target = live if item.producer_time == daily[item.origin, item.family] else history
            target.append(item)
        elif window and window.contains(item.clock):
            live.append(item)
        else:
            history.append(item)
    return {"live": live, "history": history}, window, errors


class Intake:
    """One owner per lane; transformations read immutable captured versions."""

    def __init__(self, path, *, lane, identity):
        if lane not in LANES:
            raise IntegrityError("Unknown intake lane")
        self.path, self.lane = Path(path), lane
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS inputs(
                input_id TEXT PRIMARY KEY, origin TEXT NOT NULL, family TEXT NOT NULL,
                source_path TEXT NOT NULL, sha256 TEXT NOT NULL, producer_time TEXT,
                descriptor TEXT NOT NULL, captured_at TEXT NOT NULL,
                state TEXT NOT NULL, published_at TEXT, result TEXT);
            CREATE INDEX IF NOT EXISTS pending_inputs ON inputs(state, family, producer_time);
            CREATE INDEX IF NOT EXISTS intake_source_paths ON inputs(source_path);
            CREATE TABLE IF NOT EXISTS receipts(
                receipt_id TEXT PRIMARY KEY, published_at TEXT NOT NULL, result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS failures(
                failure_id TEXT PRIMARY KEY, source_path TEXT NOT NULL, reason TEXT NOT NULL,
                first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, attempts INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS live_imports(
                input_id TEXT PRIMARY KEY, sequence INTEGER NOT NULL,
                source_path TEXT NOT NULL, producer_time TEXT,
                family TEXT NOT NULL, eligible INTEGER NOT NULL DEFAULT 0);
        """)
        expected = canonical({"lane": lane, "identity": identity})
        previous = self.setting("identity")
        if previous is not None and previous != expected:
            self.db.close()
            raise IntegrityError("Intake belongs to a different lane or storage identity")
        self.set("identity", expected)
        from .pipeline_lots import LotJournal

        self.lots = LotJournal(self)

    def close(self):
        self.db.close()

    def setting(self, key):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key, value):
        with self.db:
            self.db.execute(
                "INSERT INTO settings VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def remember(self, family, snapshot):
        identity = capture_identity(family, snapshot)
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO inputs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    snapshot.origin,
                    family,
                    snapshot.source_path,
                    snapshot.sha256,
                    snapshot.producer_time,
                    canonical(snapshot.descriptor()),
                    snapshot.received_at,
                    "CAPTURED",
                    None,
                    None,
                ),
            )
            self.lots.attach(identity, snapshot)
        (self.path.parent / "capture-intents" / (identity + ".json")).unlink(missing_ok=True)
        return identity

    def recover_captures(self, config, *, limit):
        recovered = 0
        root = self.path.parent / "capture-intents"
        for path in sorted(root.glob("*.json"))[:limit]:
            try:
                document = json.loads(path.read_text())
                cfg = replace(config, family=document["family"])
                snapshot = Snapshot(**document["snapshot"])
                if path.stem != capture_identity(cfg.family, snapshot):
                    raise IntegrityError("Capture intention identity differs")
                snapshot = recover_capture(cfg, document)
                self.remember(cfg.family, snapshot)
                recovered += 1
            except (OSError, ValueError, KeyError, TypeError, IntegrityError, CaptureError) as exc:
                self.failure(path, str(exc))
        return recovered

    def publish_captures(self, exchange, *, limit):
        from .pipeline_publications import immutable_json
        from .runtime import atomic_json

        root = exchange / "bronze"
        entries = root / "entries"
        entries.mkdir(parents=True, exist_ok=True)
        identity = json.loads(self.setting("identity"))["identity"]
        cursor = int(self.setting("published_capture_sequence") or 0)
        if (root / "current.json").exists():
            previous = json.loads((root / "current.json").read_text())
            if previous["identity"] != identity or previous["lane"] != self.lane:
                raise IntegrityError("Bronze exchange belongs to another producer")
        rows = self.db.execute(
            "SELECT rowid,* FROM inputs WHERE rowid>? ORDER BY rowid LIMIT ?", (cursor, limit)
        ).fetchall()
        for row in rows:
            payload = {
                "schema_id": "fenix-bronze-capture-v1",
                "identity": identity,
                "lane": self.lane,
                "sequence": row["rowid"],
                "input_id": row["input_id"],
                "family": row["family"],
                "snapshot": json.loads(row["descriptor"]),
            }
            value = {**payload, "capture_id": digest(payload)}
            immutable_json(entries / (str(row["rowid"]) + ".json"), value)
            atomic_json(
                root / "current.json",
                {k: value[k] for k in ("schema_id", "identity", "lane", "sequence", "capture_id")},
            )
            self.set("published_capture_sequence", str(row["rowid"]))
        return len(rows)

    def failure(self, path, reason):
        identity, stamp = digest([str(path), reason]), now()
        with self.db:
            self.db.execute(
                "INSERT INTO failures VALUES(?,?,?,?,?,1) ON CONFLICT(failure_id) DO UPDATE SET "
                "last_seen=excluded.last_seen, attempts=failures.attempts+1",
                (identity, str(path), reason, stamp, stamp),
            )

    def pending(self, family, limit, *, window=None, include_untimed=True):
        query = "SELECT input_id FROM inputs AS selected WHERE state='CAPTURED' AND family=?"
        params = [family]
        if self.lane == "live":
            query += (
                " AND NOT EXISTS (SELECT 1 FROM inputs AS newer "
                "WHERE newer.source_path=selected.source_path AND newer.family=selected.family "
                "AND newer.rowid>selected.rowid)"
            )
        else:
            query += (
                " AND NOT EXISTS (SELECT 1 FROM inputs AS older "
                "WHERE older.source_path=selected.source_path AND older.family=selected.family "
                "AND older.state='CAPTURED' AND older.rowid<selected.rowid)"
            )
            query += (
                " AND NOT EXISTS (SELECT 1 FROM live_imports AS imported "
                "WHERE imported.input_id=selected.input_id AND imported.eligible=0)"
            )
        if window is not None:
            timed = "(producer_time>=? AND producer_time<?)"
            if include_untimed:
                timed = f"(producer_time IS NULL OR length(producer_time)=10 OR {timed})"
            query += f" AND {timed}"
            params.extend((window.start.isoformat(), window.end.isoformat()))
        if self.lane == "history":
            query += (
                " AND NOT EXISTS (SELECT 1 FROM review_inputs ri JOIN historical_reviews hr "
                "USING(lot_id) WHERE ri.input_id=selected.input_id AND "
                "(hr.state='ARCHIVED_CONFIRMED' OR julianday(hr.next_at)>julianday(?)))"
            )
            params.append(self.lots.technical_now.isoformat())
            query += (
                " ORDER BY CASE WHEN EXISTS(SELECT 1 FROM review_inputs ri "
                "WHERE ri.input_id=selected.input_id) THEN 0 ELSE 1 END,producer_time ASC"
            )
            return tuple(r[0] for r in self.db.execute(query + " LIMIT ?", [*params, limit]))
        query += " ORDER BY producer_time " + ("DESC" if self.lane == "live" else "ASC")
        return tuple(r[0] for r in self.db.execute(query + " LIMIT ?", [*params, limit]))

    def acknowledge(self, ids, result, *, published_at):
        if (
            result.get("status") not in {"APPLIED", "NO_CHANGE"}
            or not result.get("complete", True)
            or result.get("remaining_versions", 0)
        ):
            return
        summary = {
            k: result[k]
            for k in ("status", "delta_version", "batch_id", "operation_id")
            if k in result
        }
        receipt = digest([list(ids), summary])
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO receipts VALUES(?,?,?)",
                (receipt, published_at, canonical(summary)),
            )
            self.db.executemany(
                "UPDATE inputs SET state='HANDLED', published_at=?, result=? "
                "WHERE input_id=? AND state='CAPTURED'",
                [(published_at, receipt, identity) for identity in ids],
            )

    def defer(self, ids, result):
        if result.get("status") in {"RETRY_WAIT", "PAUSED_RETRY_EXHAUSTED"}:
            state = "WAITING_RETRY"
        elif result.get("status") in {"BLOCKED", "PARTIAL"}:
            state = "WAITING_REVIEW"
        else:
            return
        with self.db:
            self.db.executemany(
                "UPDATE inputs SET state=? WHERE input_id=? AND state='CAPTURED'",
                [(state, identity) for identity in ids],
            )

    def release(self, family, state):
        if state not in {"WAITING_REVIEW", "WAITING_RETRY"}:
            raise IntegrityError("Only deferred captures can be released")
        with self.db:
            self.db.execute(
                "UPDATE inputs SET state='CAPTURED' WHERE family=? AND state=?", (family, state)
            )

    def capture_turn(
        self, config, policy, candidates, *, clock=time.monotonic, cursor_key="cursor"
    ):
        started = clock()
        counts = {"captured": 0, "failed": 0, "unchanged": 0, "lane": self.lane}
        selected = sorted(candidates, key=lambda c: c.order)
        cursor = json.loads(self.setting(cursor_key) or "null")
        if cursor:
            selected = [c for c in selected if c.order > tuple(cursor)] or selected
        selected = selected[: getattr(policy, f"{self.lane}_batch_files")]
        for item in selected:
            if clock() - started >= getattr(policy, f"{self.lane}_turn_seconds"):
                break
            self._capture_item(config, policy, item, counts)
            self.set(cursor_key, canonical(item.order))
        return counts

    def _capture_item(self, config, policy, item, counts, *, previous=None):
        try:
            options = {"known": {(item.origin, previous.sha256): previous}} if previous else {}
            snapshot = capture(
                replace(
                    config,
                    family=item.family,
                    stable_wait_seconds=policy.stable_wait_seconds,
                    capture_attempts=policy.capture_attempts,
                    max_file_bytes=policy.max_file_bytes,
                ),
                item.origin,
                item.path,
                item.producer_time,
                **options,
            )
            before = self.db.total_changes
            if previous is None or snapshot.sha256 != previous.sha256:
                self.remember(item.family, snapshot)
            counts["captured" if self.db.total_changes > before else "unchanged"] += 1
        except (OSError, CaptureError, IntegrityError) as exc:
            self.failure(item.path, str(exc))
            counts["failed"] += 1

    def capture_live(self, config, policy, candidates, *, window, clock=time.monotonic):
        if self.lane != "live":
            raise IntegrityError("Only live can prioritize new Fenix blocks")
        started = clock()
        paths = canonical([str(c.path) for c in candidates])
        known = dict(
            self.db.execute(
                """
            SELECT candidate.value, (
                SELECT descriptor FROM inputs WHERE source_path=candidate.value
                ORDER BY rowid DESC LIMIT 1
            ) FROM json_each(?) AS candidate
        """,
                (paths,),
            )
        )
        tiers = {"current": [], "unseen": [], "audit": []}
        for item in candidates:
            if known[str(item.path)] is not None:
                if not item.audit:
                    continue
                tier = "audit"
            elif not item.producer_time or "T" not in item.producer_time:
                tier = "unseen"
            else:
                tier = "current" if window and window.contains(item.clock) else "unseen"
            tiers[tier].append(item)
        counts = {
            "captured": 0,
            "failed": 0,
            "unchanged": 0,
            "lane": "live",
            "current_unseen": len(tiers["current"]),
            "audited": 0,
        }
        cursors = json.loads(self.setting("live_capture_cursors") or "{}")
        last, attempts = None, 0
        for tier, items in tiers.items():
            cursor = cursors.setdefault(tier, {})
            audit = tier == "audit"
            for item in balanced_candidates(items, cursor, one_per_origin=audit):
                if (
                    attempts >= policy.live_batch_files
                    or clock() - started >= policy.live_turn_seconds
                ):
                    break
                prior = known[str(item.path)]
                self._capture_item(
                    config,
                    policy,
                    item,
                    counts,
                    previous=Snapshot(**json.loads(prior)) if prior else None,
                )
                if audit:
                    counts["audited"] += 1
                attempts += 1
                last = item.order
                cursor.setdefault("after", {})[item.origin] = item.path.name
                cursor["origin"] = item.origin
        if last is not None:
            self.set("live_capture_cursors", canonical(cursors))
            self.set("cursor", canonical(last))
        return counts


def balanced_candidates(items, cursor, *, one_per_origin=False):
    """Rotate origins and their own filename cursors independently."""
    grouped = defaultdict(list)
    for item in sorted(items, key=lambda c: c.order):
        grouped[item.origin].append(item)
    last = cursor.get("origin", "")
    lanes = deque(sorted(grouped, key=lambda origin: (origin <= last, origin)))
    queues = {}
    for origin, candidates in grouped.items():
        after = cursor.get("after", {}).get(origin, "")
        queues[origin] = deque(sorted(candidates, key=lambda c: c.path.name <= after))
    while lanes:
        origin = lanes.popleft()
        queue = queues[origin]
        yield queue.popleft()
        if queue and not one_per_origin:
            lanes.append(origin)


def selected_snapshots(config):
    """Read selected immutable Bronze evidence without opening or discovering Fenix."""
    if config.intake_path is None:
        raise IntegrityError("No intake selected")
    with closing(sqlite3.connect(config.intake_path.as_uri() + "?mode=ro", uri=True)) as db:
        for identity in config.intake_ids:
            row = db.execute(
                "SELECT family, descriptor FROM inputs WHERE input_id=?", (identity,)
            ).fetchone()
            if row is None or row[0] != config.family:
                raise IntegrityError("Selected input belongs to another family or is absent")
            value = json.loads(row[1])
            snapshot = Snapshot(**value)
            original = Path(snapshot.bronze_path)
            path = original.resolve()
            if snapshot.origin not in dict(config.sources) or not path.is_relative_to(
                config.bronze_root.resolve()
            ):
                raise IntegrityError("Selected Bronze evidence escapes its origin or store")
            if original.is_symlink() or not path.is_file():
                raise IntegrityError("Selected Bronze evidence is unavailable")
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != snapshot.sha256:
                    raise IntegrityError("Selected Bronze evidence hash differs")
            yield snapshot
