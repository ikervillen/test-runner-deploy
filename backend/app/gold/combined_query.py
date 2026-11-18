"""Read-only calculation over a pair of pinned, physically independent Gold stores."""

from __future__ import annotations

from types import SimpleNamespace

from app.domain.errors import ApplicationError

from .common import IntegrityError, digest
from .evidence import combine
from .independent_inputs import cutoff
from .pipeline import build
from .query import Query


class CombinedQuery(Query):
    """Reuse the publisher's scalar engine; never combine scores or ratios."""

    def __init__(self, live_root, history_root, *, max_rows, total_rows):
        self.live = Query(live_root, max_rows=max_rows, total_rows=total_rows)
        self.history = None
        # A missing historical store cannot hold up reads of a confirmed live store.
        if (history_root / "current.json").exists():
            self.history = Query(history_root, max_rows=max_rows, total_rows=total_rows)
        pair = [q.snapshot.publication_id for q in (self.live, self.history) if q]
        self.snapshot = SimpleNamespace(publication_id=digest(pair), root=live_root)
        self.max_rows, self.total_rows = max_rows, total_rows
        self.scanned, self.files, self.cache = 0, set(), {}
        self.scan_parent, self.forks = None, {}
        self._rows = None

    def _materialize(self):
        if self._rows is not None:
            return
        if self.history is None:
            self._rows = False
            return
        from datetime import datetime

        from .common import utc_now
        from .evidence import Evidence

        inputs = []
        for query in (self.live, self.history):
            ref = next(
                (
                    d
                    for d in query.snapshot.manifest["dependencies"]
                    if d.get("kind") == "independent-input"
                ),
                None,
            )
            if ref is None:
                raise IntegrityError("Combined reads require independent Gold evidence")
            inputs.append(Evidence.open(query.snapshot.root / "evidence", ref["evidence_id"]))
        source = combine(*inputs, max_rows=self.total_rows)
        identities = {
            r["config_id"]
            for query in (self.live, self.history)
            for r in query.rows("production_calculation_state")
        }
        if len(identities) != 1:
            raise IntegrityError(
                "Combined Gold configurations need an explicit compatible revision"
            )
        configured = self.config(next(iter(identities)))
        tables, _coverage = build(
            source,
            bundle=configured["catalogs"],
            config=configured["score"],
            cutoff=cutoff(source),
            calculation_time=datetime.fromisoformat(utc_now()).replace(tzinfo=None),
            max_rows_per_source=self.max_rows,
            max_total_rows=self.total_rows,
        )
        try:
            self._rows, count = {}, 0
            for name in tables:
                rows = tables[name]
                count += len(rows)
                if count > self.total_rows:
                    raise IntegrityError("Combined Gold result exceeds its explicit row budget")
                self._rows[name] = rows
            self.scanned = sum(t.num_rows for t in source._tables.values())
        finally:
            tables.close()

    def rows(self, name, ids=None, field="id_produccion"):
        try:
            self._materialize()
            if self._rows is False:
                return self.live.rows(name, ids, field)
            values = self._rows[name]
            result = values if ids is None else [r for r in values if r.get(field) in ids]
            if len(result) > self.max_rows:
                raise IntegrityError("Combined Gold query exceeds its explicit row budget")
            return result
        except IntegrityError as exc:
            raise ApplicationError(
                "GOLD_QUERY_BUDGET" if "budget" in str(exc) else "GOLD_UNAVAILABLE",
                "La consulta conjunta excede el presupuesto o necesita revisar sus fuentes.",
                status_code=413 if "budget" in str(exc) else 503,
            ) from exc

    def fork(self, publication_id):
        # Every result was recalculated on this exact pair, including cross-boundary windows.
        if publication_id in {None, self.snapshot.publication_id}:
            return self
        for query in (self.live, self.history):
            if query and publication_id == query.snapshot.publication_id:
                return self
        raise IntegrityError("A combined result cannot use an unrelated Gold publication")

    def config(self, identity):
        for query in (self.live, self.history):
            if query and (query.snapshot.root / "configs" / f"{identity}.json").exists():
                return query.config(identity)
        raise IntegrityError("Combined Gold effective configuration is unavailable")
