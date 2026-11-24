"""Durable LISTA_RECH work units, grouped by inspection signature across captures."""

import json

from .common import IntegrityError, canonical, digest

PLAN_THRESHOLD = 1024 * 1024
UNIT_BYTES = 512 * 1024
GROUP_BYTES = 16 * 1024 * 1024


class InspectionPlan:
    def __init__(self, control, model, snapshots):
        self.db = control.db
        self.model = model
        self.identity = digest(
            ["rech-groups-v1", control.config.encoding, [(s.origin, s.sha256) for s in snapshots]]
        )
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS rech_plan (
                identity TEXT PRIMARY KEY, cursor TEXT NOT NULL, result TEXT
            );
            CREATE TABLE IF NOT EXISTS rech_plan_sources (
                identity TEXT NOT NULL, ordinal INTEGER NOT NULL, observed TEXT NOT NULL,
                PRIMARY KEY(identity,ordinal)
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS rech_plan_units (
                identity TEXT NOT NULL, signature TEXT NOT NULL, ordinal INTEGER NOT NULL,
                payload TEXT NOT NULL, size INTEGER NOT NULL,
                PRIMARY KEY(identity,signature,ordinal)
            ) WITHOUT ROWID;
        """)

    def stage(self, snapshots, encoding):
        if self.state() is not None:
            return
        for ordinal, snapshot in enumerate(snapshots):
            if self.db.execute(
                "SELECT 1 FROM rech_plan_sources WHERE identity=? AND ordinal=?",
                (self.identity, ordinal),
            ).fetchone():
                continue
            parsed = self.model.parse([snapshot], encoding)
            with self.db:
                self.db.executemany(
                    "INSERT INTO rech_plan_units VALUES(?,?,?,?,?)",
                    self._units(parsed, ordinal),
                )
                self.db.execute(
                    "INSERT INTO rech_plan_sources VALUES(?,?,?)",
                    (self.identity, ordinal, canonical(parsed.observed)),
                )
        with self.db:
            self.db.execute("INSERT INTO rech_plan VALUES(?, '', NULL)", (self.identity,))

    def _units(self, parsed, ordinal):
        groups = {}
        for entry, refs in parsed.entries.items():
            row = {"origen_fenix": entry[0], **dict(zip(self.model.FIELDS, entry[1:], strict=True))}
            key = "g" + digest(self.model.group(row))
            groups.setdefault(key, [[], []])[0].append((entry, refs))
        for i, issue in enumerate(parsed.issues):
            key = "g" + digest(issue["group"]) if "group" in issue else f"a{ordinal:016x}{i:016x}"
            groups.setdefault(key, [[], []])[1].append(issue)
        for key, payload in groups.items():
            value = canonical(payload)
            yield self.identity, key, ordinal, value, len(value.encode("utf-8"))

    def state(self):
        return self.db.execute(
            "SELECT cursor,result FROM rech_plan WHERE identity=?", (self.identity,)
        ).fetchone()

    def next_unit(self, after):
        keys, size = [], 0
        for key, weight in self.db.execute(
            "SELECT signature,sum(size) FROM rech_plan_units WHERE identity=? AND signature>? "
            "GROUP BY signature ORDER BY signature",
            (self.identity, after),
        ):
            if weight > GROUP_BYTES:
                raise IntegrityError("One LISTA_RECH inspection group exceeds its recovery budget")
            if keys and size + weight > UNIT_BYTES:
                break
            keys.append(key)
            size += weight
            if len(keys) >= 128:
                break
        return keys

    def load(self, keys):
        result = self.model.Parsed()
        for key in keys:
            for (payload,) in self.db.execute(
                "SELECT payload FROM rech_plan_units WHERE identity=? AND signature=? "
                "ORDER BY ordinal",
                (self.identity, key),
            ):
                entries, issues = json.loads(payload)
                for entry, refs in entries:
                    result.entries.setdefault(tuple(entry), []).extend(refs)
                    result.rows_read += sum(1 + r["duplicate_count"] for r in refs)
                for issue in issues:
                    if "group" in issue:
                        result.invalid_groups.add(tuple(issue["group"]))
                    if issue["reason"] in {"STRUCTURE", "INVALID_VALUE"}:
                        result.rows_read += 1
                        result.invalid_rows += 1
                result.issues.extend(issues)
        return result

    def observed(self):
        result = {}
        for (observed,) in self.db.execute(
            "SELECT observed FROM rech_plan_sources WHERE identity=?", (self.identity,)
        ):
            for origin, stamp in json.loads(observed).items():
                result[origin] = max(result.get(origin, stamp), stamp)
        return result

    def checkpoint(self, cursor, result):
        with self.db:
            self.db.execute(
                "UPDATE rech_plan SET cursor=?,result=? WHERE identity=?",
                (cursor, canonical(result), self.identity),
            )

    def discard(self):
        # These are re-creatable staging copies. Bronze, receipts and Delta remain immutable.
        with self.db:
            for table in ("rech_plan_units", "rech_plan_sources", "rech_plan"):
                self.db.execute(f"DELETE FROM {table} WHERE identity=?", (self.identity,))
