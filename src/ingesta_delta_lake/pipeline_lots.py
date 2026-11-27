"""Lane-owned lot journal and immutable handoff; no shared mutable control database."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from .common import IntegrityError, digest
from .pipeline_publications import immutable_json
from .runtime import atomic_json


def read_manifest(storage, name):
    root = storage.exchange / name
    pointer = root / "current.json"
    if not pointer.exists():
        return None
    identity = json.loads(pointer.read_text(encoding="utf-8"))["publication_id"]
    if (
        not isinstance(identity, str)
        or len(identity) != 64
        or any(c not in "0123456789abcdef" for c in identity)
    ):
        raise IntegrityError("Invalid lot publication identity")
    value = json.loads((root / (identity + ".json")).read_text(encoding="utf-8"))
    if digest(value) != identity or value.get("schema_id") != "fenix-lots-v1":
        raise IntegrityError("Lot publication changed")
    if name == "lots":
        records = []
        for ref in value["lots"]:
            if (
                not isinstance(ref["hash"], str)
                or len(ref["hash"]) != 64
                or any(c not in "0123456789abcdef" for c in ref["hash"])
            ):
                raise IntegrityError("Invalid lot record hash")
            record = json.loads((root / "records" / (ref["hash"] + ".json")).read_text())
            if digest(record) != ref["hash"] or record["lot_id"] != ref["lot_id"]:
                raise IntegrityError("Lot record differs from its publication")
            records.append(record)
        value = {**value, "lots": records}
    return value


def accepted_input(result, identity):
    return (
        not result.get("remaining_versions", 0)
        and not result.get("publication_error")
        and (
            (result.get("status") in {"APPLIED", "NO_CHANGE"} and result.get("complete", True))
            or (
                result.get("status") == "PARTIAL"
                and identity in result.get("confirmed_input_ids", ())
            )
        )
    )


def publish(storage, name, value):
    root = storage.exchange / name
    root.mkdir(parents=True, exist_ok=True)
    if name == "lots":
        records = []
        (root / "records").mkdir(exist_ok=True)
        for lot in value["lots"]:
            identity = digest(lot)
            immutable_json(root / "records" / (identity + ".json"), lot)
            records.append({"lot_id": lot["lot_id"], "hash": identity})
        value = {**value, "lots": records}
    value = {"schema_id": "fenix-lots-v1", **value}
    identity = digest(value)
    immutable_json(root / (identity + ".json"), value)
    pointer = {"publication_id": identity}
    path = root / "current.json"
    if not path.exists() or json.loads(path.read_text()) != pointer:
        atomic_json(path, pointer)
    return identity


class LotJournal:
    def __init__(self, intake):
        self.intake, self.db = intake, intake.db
        self.technical_now = datetime.now(UTC)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS lots(
                lot_id TEXT PRIMARY KEY, origin TEXT NOT NULL, start TEXT NOT NULL,
                end TEXT NOT NULL, first_attempt TEXT, first_publication TEXT,
                retry_at TEXT, retry_used INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL DEFAULT 'NEW');
            CREATE TABLE IF NOT EXISTS lot_inputs(
                input_id TEXT PRIMARY KEY, lot_id TEXT NOT NULL, confirmation TEXT);
            CREATE INDEX IF NOT EXISTS lot_members ON lot_inputs(lot_id);
            CREATE INDEX IF NOT EXISTS lot_source_windows ON lots(origin,start);
            CREATE INDEX IF NOT EXISTS active_lots ON lots(state);
            CREATE TABLE IF NOT EXISTS historical_reviews(
                lot_id TEXT PRIMARY KEY, origin TEXT NOT NULL, start TEXT NOT NULL,
                end TEXT NOT NULL, request TEXT NOT NULL, state TEXT NOT NULL,
                next_at TEXT NOT NULL, result TEXT);
            CREATE TABLE IF NOT EXISTS review_inputs(
                input_id TEXT PRIMARY KEY, lot_id TEXT NOT NULL);
        """)

    def frontier(self, pipelines, window):
        """Live owns the initial global boundary; history only accepts its publication."""
        if self.intake.lane == "live":
            previous = self.intake.setting("initial_frontier")
            if previous is None and window:
                previous = window.start.isoformat()
                self.intake.set("initial_frontier", previous)
            return datetime.fromisoformat(previous) if previous else None
        value = read_manifest(pipelines.live, "lots")
        if value is None:
            return None
        owner = self.intake.setting("lot_owner")
        if owner not in (None, value["owner"]):
            raise IntegrityError("Live lot ownership changed; migrate explicitly")
        previous = self.intake.setting("initial_frontier")
        if previous not in (None, value["frontier"]):
            raise IntegrityError("The initial global frontier cannot move")
        self.intake.set("lot_owner", value["owner"])
        if value["frontier"]:
            self.intake.set("initial_frontier", value["frontier"])
        return datetime.fromisoformat(value["frontier"]) if value["frontier"] else None

    def attach(self, identity, snapshot):
        from .sources import SILVER_EXPORTS

        family = self.db.execute(
            "SELECT family FROM inputs WHERE input_id=?", (identity,)
        ).fetchone()[0]
        if family not in SILVER_EXPORTS:
            return
        stamp = snapshot.producer_time
        frontier = self.intake.setting("initial_frontier")
        if not stamp or "T" not in stamp or not frontier or stamp < frontier:
            return
        policy_json = self.intake.setting("lot_policy")
        if policy_json is None:
            return
        from .pipeline_policy import PipelinePolicy

        policy = PipelinePolicy(**json.loads(policy_json))
        window = policy.source_window(datetime.fromisoformat(stamp))
        start, end = window.start.isoformat(), window.end.isoformat()
        if self.intake.lane == "history":
            review = self.db.execute(
                "SELECT lot_id FROM historical_reviews WHERE origin=? AND start=?",
                (snapshot.origin, start),
            ).fetchone()
            if review:
                self.db.execute(
                    "INSERT OR IGNORE INTO review_inputs VALUES(?,?)", (identity, review[0])
                )
                self.db.execute(
                    "UPDATE inputs SET state='SUPERSEDED' WHERE source_path=? "
                    "AND rowid<(SELECT rowid FROM inputs WHERE input_id=?) "
                    "AND state!='HANDLED' AND input_id IN (SELECT input_id FROM review_inputs)",
                    (snapshot.source_path, identity),
                )
            return
        lot_id = digest([snapshot.origin, start, end])
        self.db.execute(
            "INSERT OR IGNORE INTO lots(lot_id,origin,start,end) VALUES(?,?,?,?)",
            (lot_id, snapshot.origin, start, end),
        )
        self.db.execute("INSERT OR IGNORE INTO lot_inputs VALUES(?,?,NULL)", (identity, lot_id))

    def owned_candidates(self, candidates, policy, window, frontier):
        selected = []
        states = (
            {
                (origin, start): state
                for origin, start, state in self.db.execute(
                    "SELECT origin,start,state FROM lots WHERE start>=?", (frontier.isoformat(),)
                )
            }
            if frontier is not None and self.intake.lane == "live"
            else {}
        )
        for item in candidates:
            if not item.producer_time or "T" not in item.producer_time:
                selected.append(item)
                continue
            if frontier is None:
                continue
            if self.intake.lane == "history":
                if item.clock < frontier:
                    selected.append(item)
                continue
            if item.clock < frontier:
                continue
            start = policy.source_window(item.clock).start.isoformat()
            state = states.get((item.origin, start))
            if state in {None, "NEW", "PROCESSING", "RETRYING"}:
                selected.append(item)
            elif window and window.contains(item.clock):
                if state == "PUBLISHED":
                    selected.append(item)
                elif state in {"WAITING_HOUR_RETRY", "WAITING_HISTORY_REVIEW"}:
                    # A failed family must not hide newly arrived files of the same block.
                    # Existing captures stay deferred until their scheduled retry.
                    selected.append(replace(item, audit=False))
        return selected

    def members(self, lot_id):
        return self.db.execute(
            "SELECT i.*,m.confirmation FROM inputs i JOIN lot_inputs m USING(input_id) "
            "WHERE m.lot_id=? AND NOT EXISTS(SELECT 1 FROM inputs n "
            "WHERE n.source_path=i.source_path AND n.rowid>i.rowid)",
            (lot_id,),
        ).fetchall()

    def claim(self, ids, policy, stamp):
        """Persist first attempt before starting the subprocess; never reset ageing."""
        for identity in ids:
            row = self.db.execute(
                "SELECT l.* FROM lots l JOIN lot_inputs m USING(lot_id) WHERE m.input_id=?",
                (identity,),
            ).fetchone()
            if not row or row["first_attempt"]:
                continue
            local = datetime.fromisoformat(row["start"])
            midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
            elapsed = (local - midnight).total_seconds()
            boundary = midnight + timedelta(
                seconds=(elapsed // policy.live_retry_period_seconds + 1)
                * policy.live_retry_period_seconds
            )
            due = boundary.replace(tzinfo=ZoneInfo(policy.timezone)).astimezone(UTC)
            with self.db:
                self.db.execute(
                    "UPDATE lots SET first_attempt=?,retry_at=?,state='PROCESSING' WHERE lot_id=?",
                    (stamp.isoformat(), due.isoformat(), row["lot_id"]),
                )

    def record(self, ids, result, manifest, stamp, policy):
        if result.get("remaining_versions", 0) and not result.get("publication_error"):
            return
        with self.db:
            for identity in ids:
                unit = self.db.execute(
                    "SELECT l.lot_id,i.family FROM lot_inputs l JOIN inputs i USING(input_id) "
                    "WHERE input_id=?",
                    (identity,),
                ).fetchone()
                reviewed = self.db.execute(
                    "SELECT lot_id FROM review_inputs WHERE input_id=?", (identity,)
                ).fetchone()
                if not unit and not reviewed:
                    continue
                if not accepted_input(result, identity):
                    self.db.execute(
                        "UPDATE inputs SET state='LOT_WAIT',result=? WHERE input_id=?",
                        (json.dumps(result), identity),
                    )
                if unit and manifest and unit["family"] in manifest["tables"]:
                    ref = manifest["tables"][unit["family"]]
                    self.db.execute(
                        "UPDATE lot_inputs SET confirmation=? WHERE input_id=?",
                        (
                            json.dumps({"table": ref["table_id"], "version": ref["version"]}),
                            identity,
                        ),
                    )
                    self.db.execute(
                        "UPDATE lots SET first_publication=coalesce(first_publication,?) "
                        "WHERE lot_id=?",
                        (stamp.isoformat(), unit["lot_id"]),
                    )
                if reviewed:
                    waiting = self.db.execute(
                        "SELECT 1 FROM inputs i JOIN review_inputs r USING(input_id) "
                        "WHERE r.lot_id=? AND i.state='CAPTURED' LIMIT 1",
                        (reviewed[0],),
                    ).fetchone()
                    due = (
                        stamp
                        if waiting
                        else stamp + timedelta(seconds=policy.history_review_retry_seconds)
                    )
                    self.db.execute(
                        "UPDATE historical_reviews SET next_at=?,result=? WHERE lot_id=?",
                        (
                            due.isoformat(),
                            json.dumps(result),
                            reviewed[0],
                        ),
                    )

    def advance(self, pipelines, stamp, *, active_ids=(), archive_enabled=True):
        self.technical_now = stamp
        policy = pipelines.policy
        if self.intake.lane == "history":
            return
        completed, reviewed = {}, set()
        if archive_enabled:
            completed, reviewed = self._archive_status(pipelines)
        active = set(active_ids)
        self._advance_live(policy, stamp, active, completed, reviewed, archive_enabled)

    def _archive_status(self, pipelines):
        completed, reviewed = {}, set()
        try:
            from .pipeline_view import ArchiveSnapshot

            completed = ArchiveSnapshot(pipelines.history).manifest["completed"]
        except (OSError, ValueError, IntegrityError, KeyError):
            pass  # A missing/corrupt history publication cannot hold up new live inputs.
        try:
            receipts = read_manifest(pipelines.history, "lot-reviews")
            if receipts:
                owner = self.intake.setting("history_lot_owner")
                if owner not in (None, receipts["owner"]):
                    raise IntegrityError("Historical lot owner changed")
                self.intake.set("history_lot_owner", receipts["owner"])
                reviewed = {
                    r["lot_id"] for r in receipts["reviews"] if r["state"] == "ARCHIVED_CONFIRMED"
                }
        except (OSError, ValueError, IntegrityError, KeyError):
            pass
        return completed, reviewed

    def _advance_live(self, policy, stamp, active, completed, reviewed, archive_enabled):
        for row in self.db.execute(
            "SELECT * FROM lots WHERE state!='ARCHIVED_CONFIRMED'"
        ).fetchall():
            units = self.members(row["lot_id"])
            if not units:
                continue
            if row["state"] == "ARCHIVED_CONFIRMED":
                continue
            states = {u["state"] for u in units}
            if row["first_attempt"] is None:
                continue
            origin = row["first_publication"] or row["first_attempt"]
            expired = (
                archive_enabled
                and (stamp - datetime.fromisoformat(origin)).total_seconds()
                >= policy.retention_seconds
            )
            busy = any(u["input_id"] in active for u in units)
            if busy and expired:
                continue
            failed = bool(states - {"HANDLED"})
            confirmed = [json.loads(u["confirmation"]) for u in units if u["confirmation"]]
            copied = all(completed.get(c["table"], -1) >= c["version"] for c in confirmed)
            with self.db:
                if expired and copied and (not failed or row["lot_id"] in reviewed):
                    state = "ARCHIVED_CONFIRMED"
                elif row["state"] == "TRANSFERRED_FOR_REVIEW":
                    continue
                elif failed and expired:
                    state = "TRANSFERRED_FOR_REVIEW"
                    self.db.executemany(
                        "UPDATE inputs SET state='TRANSFERRED' "
                        "WHERE input_id=? AND state!='HANDLED'",
                        [(u["input_id"],) for u in units],
                    )
                elif (
                    "LOT_WAIT" in states
                    and not row["retry_used"]
                    and stamp >= datetime.fromisoformat(row["retry_at"])
                ):
                    state = "RETRYING"
                    self.db.execute("UPDATE lots SET retry_used=1 WHERE lot_id=?", (row["lot_id"],))
                    self.db.executemany(
                        "UPDATE inputs SET state='CAPTURED' WHERE input_id=? AND state='LOT_WAIT'",
                        [(u["input_id"],) for u in units],
                    )
                elif busy:
                    continue
                elif "CAPTURED" in states:
                    state = "RETRYING" if row["retry_used"] else "PROCESSING"
                elif failed:
                    state = "WAITING_HISTORY_REVIEW" if row["retry_used"] else "WAITING_HOUR_RETRY"
                else:
                    state = "PUBLISHED"
                self.db.execute("UPDATE lots SET state=? WHERE lot_id=?", (state, row["lot_id"]))

    def publish(self, storage):
        if self.intake.lane == "history":
            return publish(
                storage,
                "lot-reviews",
                {
                    "owner": json.loads(self.intake.setting("identity"))["identity"],
                    "reviews": [
                        dict(r)
                        for r in self.db.execute("SELECT * FROM historical_reviews ORDER BY lot_id")
                    ],
                },
            )
        lots = []
        for row in self.db.execute(
            "SELECT * FROM lots WHERE state!='ARCHIVED_CONFIRMED' ORDER BY lot_id"
        ):
            lots.append(
                {
                    **dict(row),
                    "inputs": [
                        {
                            k: u[k]
                            for k in (
                                "input_id",
                                "family",
                                "source_path",
                                "descriptor",
                                "state",
                                "confirmation",
                            )
                        }
                        for u in self.members(row["lot_id"])
                    ],
                }
            )
        return publish(
            storage,
            "lots",
            {
                "owner": json.loads(self.intake.setting("identity"))["identity"],
                "frontier": self.intake.setting("initial_frontier"),
                "lots": lots,
            },
        )

    def refresh_reviews(self, pipelines, stamp):
        """History imports failed-lot requests, never live's checkpoint or locks."""
        value = read_manifest(pipelines.live, "lots")
        if value is None:
            return []
        allowed = []
        with self.db:
            for lot in value["lots"]:
                if lot["state"] != "TRANSFERRED_FOR_REVIEW":
                    continue
                self.db.execute(
                    "INSERT OR IGNORE INTO historical_reviews VALUES(?,?,?,?,?,'PENDING',?,NULL)",
                    (
                        lot["lot_id"],
                        lot["origin"],
                        lot["start"],
                        lot["end"],
                        json.dumps(lot),
                        stamp.isoformat(),
                    ),
                )
            for row in self.db.execute(
                "SELECT * FROM historical_reviews WHERE state!='ARCHIVED_CONFIRMED' AND next_at<=?",
                (stamp.isoformat(),),
            ).fetchall():
                lot = json.loads(row["request"])
                allowed.append(lot)
                for unit in lot["inputs"]:
                    if unit["state"] == "HANDLED":
                        continue
                    if self.db.execute(
                        "SELECT 1 FROM inputs WHERE input_id=?", (unit["input_id"],)
                    ).fetchone():
                        self.db.execute(
                            "INSERT OR IGNORE INTO review_inputs VALUES(?,?)",
                            (unit["input_id"], row["lot_id"]),
                        )
                busy = self.db.execute(
                    "SELECT 1 FROM inputs i JOIN review_inputs r USING(input_id) "
                    "WHERE r.lot_id=? AND i.state='CAPTURED' LIMIT 1",
                    (row["lot_id"],),
                ).fetchone()
                if not busy:
                    self.db.execute(
                        "UPDATE inputs SET state='CAPTURED' "
                        "WHERE state IN ('LOT_WAIT','TRANSFERRED') "
                        "AND input_id IN (SELECT input_id FROM review_inputs WHERE lot_id=?)",
                        (row["lot_id"],),
                    )
                self.db.execute(
                    "UPDATE live_imports SET eligible=1 WHERE input_id IN "
                    "(SELECT input_id FROM review_inputs WHERE lot_id=?)",
                    (row["lot_id"],),
                )
        return allowed

    def finish_reviews(self, storage):
        from .pipeline_view import ArchiveSnapshot

        try:
            completed = ArchiveSnapshot(storage).manifest["completed"]
        except (OSError, ValueError, IntegrityError, KeyError):
            completed = {}
        with self.db:
            for row in self.db.execute(
                "SELECT * FROM historical_reviews WHERE state!='ARCHIVED_CONFIRMED'"
            ).fetchall():
                units = self.db.execute(
                    "SELECT i.* FROM inputs i JOIN review_inputs m USING(input_id) "
                    "WHERE m.lot_id=? AND NOT EXISTS(SELECT 1 FROM inputs n "
                    "WHERE n.source_path=i.source_path AND n.rowid>i.rowid)",
                    (row["lot_id"],),
                ).fetchall()
                expected = [
                    u for u in json.loads(row["request"])["inputs"] if u["state"] != "HANDLED"
                ]
                confirmations = [
                    json.loads(u["confirmation"])
                    for u in json.loads(row["request"])["inputs"]
                    if u["confirmation"]
                ]
                copies_verified = all(
                    completed.get(c["table"], -1) >= c["version"] for c in confirmations
                )
                paths = {u["source_path"] for u in units if u["state"] == "HANDLED"}
                if (
                    expected
                    and copies_verified
                    and all(u["state"] == "HANDLED" for u in units)
                    and all(u["source_path"] in paths for u in expected)
                ):
                    self.db.execute(
                        "UPDATE historical_reviews SET state='ARCHIVED_CONFIRMED' WHERE lot_id=?",
                        (row["lot_id"],),
                    )
        self.publish(storage)

    def pending_window(self, ids, policy):
        if not ids:
            return None
        row = self.db.execute(
            "SELECT producer_time FROM inputs WHERE input_id=?", (ids[0],)
        ).fetchone()
        return (
            policy.source_window(datetime.fromisoformat(row[0]))
            if row and row[0] and "T" in row[0]
            else None
        )

    def managed(self, ids):
        return any(
            self.db.execute(
                "SELECT 1 FROM lot_inputs WHERE input_id=? "
                "UNION SELECT 1 FROM review_inputs WHERE input_id=?",
                (identity, identity),
            ).fetchone()
            for identity in ids
        )

    def review_ids(self, ids):
        if not ids:
            return ids
        row = self.db.execute(
            "SELECT lot_id FROM review_inputs WHERE input_id=?", (ids[0],)
        ).fetchone()
        if not row:
            return ids
        members = {
            r[0] for r in self.db.execute("SELECT input_id FROM review_inputs WHERE lot_id=?", row)
        }
        return tuple(identity for identity in ids if identity in members)
