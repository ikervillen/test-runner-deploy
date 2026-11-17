"""Indexed pending queue and lazy identities; never load consumed history into RAM."""

import json
from collections.abc import Set

from .common import IntegrityError
from .sources import Snapshot


class Captures:
    def __init__(self, db):
        self.db = db

    def get(self, key, default=None):
        row = self.db.execute(
            "SELECT descriptor_json FROM input_versions WHERE origin=? AND sha256=?", key
        ).fetchone()
        return Snapshot(**json.loads(row[0])) if row else default

    def __contains__(self, key):
        return (
            self.db.execute(
                "SELECT 1 FROM input_versions WHERE origin=? AND sha256=?", key
            ).fetchone()
            is not None
        )

    def setdefault(self, key, snapshot):
        return self.get(key, snapshot)

    def __setitem__(self, key, snapshot):
        if key != (snapshot.origin, snapshot.sha256) or key not in self:
            raise IntegrityError("Capture must be durably enqueued before use")

    def update(self, values):
        for key, value in values.items():
            self[key] = value

    def items(self):
        for origin, sha, raw in self.db.execute(
            "SELECT p.origin,p.sha256,v.descriptor_json FROM pending_inputs p "
            "JOIN input_versions v USING(origin,sha256) ORDER BY p.rowid"
        ):
            yield (origin, sha), Snapshot(**json.loads(raw))

    def values(self):
        """Every registered file, including ones already stored. The live clock needs them."""
        for (raw,) in self.db.execute("SELECT descriptor_json FROM input_versions ORDER BY rowid"):
            yield Snapshot(**json.loads(raw))


class Consumed(Set):
    def __init__(self, db):
        self.db = db

    def __contains__(self, key):
        return (
            self.db.execute(
                "SELECT 1 FROM consumed_inputs WHERE origin=? AND sha256=?", key
            ).fetchone()
            is not None
        )

    def __len__(self):
        return self.db.execute("SELECT count(*) FROM consumed_inputs").fetchone()[0]

    def __iter__(self):
        for row in self.db.execute("SELECT origin,sha256 FROM consumed_inputs"):
            yield tuple(row)
