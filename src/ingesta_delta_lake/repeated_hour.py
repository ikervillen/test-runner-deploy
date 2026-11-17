"""Approved lossy policy: latest export replaces the repeated local-hour sample.

Only the existing family writer executes this policy. Bronze and Delta history
retain the original evidence; active Silver deliberately retains one occurrence.
"""

import hashlib
import json
import os
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .common import IntegrityError, canonical, digest, now
from .review_adapters import KEEP, REASONS, REPLACE, adapter
from .reviews import _execute, state_hash
from .sources import Snapshot

RULE = "repeated_hour_latest_export_v1"
FAMILIES = {"LISTA_RECH", "LISTA_DEFECTOS", "CONT_ZC"}
ZONE = os.environ.get("INGESTA_SOURCE_TIMEZONE", "Europe/Madrid")


def repeated(stamp):
    value = datetime.fromisoformat(stamp)
    zone = ZoneInfo(ZONE)
    first = value.replace(tzinfo=zone, fold=0).utcoffset()
    second = value.replace(tzinfo=zone, fold=1).utcoffset()
    return first is not None and second is not None and first > second


def _initialize(control):
    control.db.execute(
        "CREATE TABLE IF NOT EXISTS repeated_hour_slots("
        "origin TEXT NOT NULL,stamp TEXT NOT NULL,source_hash TEXT NOT NULL,"
        "decision_id TEXT NOT NULL,PRIMARY KEY(origin,stamp))"
    )


def confirm(control, command, plan):
    _initialize(control)
    control.db.executemany(
        "INSERT INTO repeated_hour_slots VALUES(?,?,?,?) ON CONFLICT(origin,stamp) "
        "DO UPDATE SET source_hash=excluded.source_hash,decision_id=excluded.decision_id",
        [
            (s["origin"], s["stamp"], s["sha256"], command["decision_id"])
            for s in plan["policy_slots"]
        ],
    )
    # Full replacement rows are needed only while a commit is uncertain.
    compact = {k: v for k, v in plan.items() if k != "overwrite_rows"}
    control.db.execute(
        "UPDATE review_operations SET plan_json=? WHERE decision_id=?",
        (canonical(compact), command["decision_id"]),
    )


def _snapshots(control):
    table = "input_versions" if control.config.family != "CONT_ZC" else "capture_cache"
    for (text,) in control.db.execute(f"SELECT descriptor_json FROM {table} ORDER BY rowid"):
        snap = Snapshot(**json.loads(text))
        exported = datetime.fromtimestamp(snap.mtime_ns / 1_000_000_000, UTC).astimezone(
            ZoneInfo(ZONE)
        )
        stamp = snap.producer_time or exported.replace(tzinfo=None).isoformat(timespec="seconds")
        if not repeated(stamp):
            continue
        # File modification is ordering evidence, never the timestamp of every row.
        if (
            abs((exported.replace(tzinfo=None) - datetime.fromisoformat(stamp)).total_seconds())
            > 120
        ):
            continue
        if (
            table == "input_versions"
            and control.compact
            and control.db.execute(
                "SELECT 1 FROM input_fragments WHERE origin=? AND child_sha=? LIMIT 1",
                (snap.origin, snap.sha256),
            ).fetchone()
        ):
            continue  # Never replace a whole sample with a partial input fragment.
        yield snap, stamp


def _business_rows(payload):
    accepted = payload.get("accepted") or []
    if isinstance(accepted, dict):
        accepted = [accepted]
    return [
        *accepted,
        *payload.get("candidates", []),
        *([payload["candidate"]] if "candidate" in payload else []),
    ]


def _order(snapshot):
    return snapshot.mtime_ns, datetime.fromisoformat(snapshot.received_at).timestamp()


def _replace(config, control, a, origin, winners):
    stamps = sorted(winners)
    scope = [[origin, stamps[0], stamps[-1]]] if config.family != "CONT_ZC" else None
    if scope:
        a.store.set_scope(scope)
    version, existing = a.store.read()
    rows = a.rows(existing)
    if version is None or str(version) != control.setting("last_version"):
        return 0
    selected = set(stamps)
    old_rows = {
        k: r for k, r in rows.items() if r["origen_fenix"] == origin and r["FECHA"] in selected
    }
    replacements = {a.key(r): r for _, values in winners.values() for r in values}
    if len(replacements) != sum(len(values) for _, values in winners.values()):
        return 0  # A source contradicting itself is not a usable replacement.
    if config.family in {"LISTA_RECH", "LISTA_DEFECTOS"}:
        if any(
            len({r["INSPECC"] for r in values}) > 1
            for index in a.model.indexes(replacements.values())[1:]
            for values in index.values()
        ):
            return 0
    after = {k: r for k, r in rows.items() if k not in old_rows}
    if set(after) & set(replacements):
        raise IntegrityError("Repeated-hour replacement collides with another observation")
    after.update(replacements)
    a.validate(after)
    close, reject = [], {a.signature(r) for r in old_rows.values()}
    for issue in control.db.execute(
        "SELECT issue_id,reason,payload_json FROM issues WHERE status!='RESOLVED'"
    ):
        if issue[1] not in REASONS:
            continue
        candidates = _business_rows(json.loads(issue[2]))
        if candidates and all(
            r.get("origen_fenix") == origin and r.get("FECHA") in selected for r in candidates
        ):
            close.append(issue[0])
            reject.update(a.signature(r) for r in candidates)
    reject -= {a.signature(r) for r in replacements.values()}
    slots = [
        {
            "origin": origin,
            "stamp": stamp,
            "sha256": snap.sha256,
            "source_path": snap.source_path,
            "source_mtime_ns": snap.mtime_ns,
        }
        for stamp, (snap, _) in sorted(winners.items())
    ]
    old = next(iter(old_rows.values()), None)
    candidate = next(iter(replacements.values()))
    decision_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            digest([RULE, control.control_id, version, slots, sorted(close), sorted(reject)]),
        )
    )
    command = {
        "decision_id": decision_id,
        "projection_issue_id": digest([RULE, origin, stamps]),
        "source_issue_id": close[0] if close else digest([RULE, slots]),
        "environment": config.environment,
        "plant": config.plant,
        "family": config.family,
        "control_id": control.control_id,
        "action_id": REPLACE if rows != after else KEEP,
        "automatic_policy": RULE,
        "authorized_by": "user-approved-policy-2026-10-26",
    }

    def quote(value):
        return "'" + value.replace("'", "''") + "'"

    predicate = (
        "origen_fenix = "
        + quote(origin)
        + " AND FECHA IN ("
        + ",".join(quote(s) for s in stamps)
        + ")"
    )
    plan = {
        "before_version": version,
        "archive_context": config.archive_context,
        "scope": scope,
        "old": old,
        "candidate": candidate,
        "before_hash": state_hash(rows),
        "after_hash": state_hash(after),
        "reject": sorted(reject),
        "close": close,
        "policy_slots": slots,
        "replaced_rows": len(old_rows),
        "overwrite_predicate": predicate,
        "overwrite_rows": list(replacements.values()),
        "removed_group_ids": sorted(
            {digest(a.model.group(r)) for r in rows.values()}
            - {digest(a.model.group(r)) for r in after.values()}
        )
        if scope
        else [],
    }
    with control.db:
        control.db.execute(
            "INSERT INTO review_operations VALUES(?,?,?,'PREPARED',?,NULL,?,NULL)",
            (decision_id, digest(command), canonical(command), canonical(plan), now()),
        )
    _execute(config, control, command, plan)
    return len(stamps)


def process(config, control, result):
    if config.family not in FAMILIES or control.pending():
        return result
    _initialize(control)
    a = adapter(config, control.control_id)
    if not a.store.path.exists():
        return result
    table = "input_versions" if config.family != "CONT_ZC" else "capture_cache"
    source_stamp = tuple(control.db.execute(f"SELECT count(*),max(rowid) FROM {table}").fetchone())
    fingerprint = canonical(
        [RULE, ZONE, control.setting("last_version"), source_stamp, control.setting("review_epoch")]
    )
    if control.setting("repeated_hour_checked") == fingerprint:
        return result
    winners = defaultdict(dict)
    applied = {
        (r[0], r[1]): r[2]
        for r in control.db.execute("SELECT origin,stamp,source_hash FROM repeated_hour_slots")
    }
    pending_slots = set()
    for (text,) in control.db.execute("SELECT payload_json FROM issues WHERE status!='RESOLVED'"):
        pending_slots.update(
            (r.get("origen_fenix"), r.get("FECHA")) for r in _business_rows(json.loads(text))
        )
    for snap, exported_stamp in _snapshots(control):
        path = Path(snap.bronze_path).resolve()
        if (
            not path.is_relative_to(config.bronze_root)
            or hashlib.sha256(path.read_bytes()).hexdigest() != snap.sha256
        ):
            raise IntegrityError("Repeated-hour evidence does not match immutable Bronze")
        parsed = a.model.parse([snap], config.encoding)
        if parsed.issues:
            continue
        by_stamp = defaultdict(list)
        for entry in parsed.entries:
            row = {"origen_fenix": entry[0], **dict(zip(a.model.FIELDS, entry[1:], strict=True))}
            stamp = row["FECHA"]
            if (
                repeated(stamp)
                and abs(
                    (
                        datetime.fromisoformat(stamp) - datetime.fromisoformat(exported_stamp)
                    ).total_seconds()
                )
                < 600
            ):
                by_stamp[stamp].append(row)
        for stamp, values in by_stamp.items():
            key = (snap.origin, stamp[:10])
            prior = winners[key].get(stamp)
            if prior is None or _order(snap) > _order(prior[0]):
                winners[key][stamp] = snap, values
            elif _order(snap) == _order(prior[0]) and snap.sha256 != prior[0].sha256:
                raise IntegrityError("Repeated-hour exports have ambiguous equal ordering evidence")
    corrected = 0
    for (origin, _), slots in sorted(winners.items()):
        slots = {
            s: v
            for s, v in slots.items()
            if applied.get((origin, s)) != v[0].sha256 or (origin, s) in pending_slots
        }
        if slots:
            corrected += _replace(config, control, a, origin, slots)
    with control.db:
        final = canonical(
            [
                RULE,
                ZONE,
                control.setting("last_version"),
                source_stamp,
                control.setting("review_epoch"),
            ]
        )
        control.db.execute(
            "INSERT INTO settings VALUES('repeated_hour_checked',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (final,),
        )
    if not corrected:
        return result
    pending = control.db.execute("SELECT count(*) FROM issues WHERE status!='RESOLVED'").fetchone()[
        0
    ]
    complete = pending == 0 and not result.get("remaining_versions", 0)
    if config.family in {"LISTA_RECH", "LISTA_DEFECTOS"}:
        complete = complete and control.inspection_summary()["unresolved_groups"] == 0
        from .mia_scope import files

        result = {**result, "published_rows": sum(f[2] for f in files(a.store.table()))}
    return {
        **result,
        "delta_version": int(control.setting("last_version")),
        "complete": complete,
        "quality_complete": pending == 0,
        "status": "APPLIED" if complete else "PARTIAL",
        "automatic_repeated_hour_slots": corrected,
    }
