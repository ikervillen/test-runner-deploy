"""Self-contained, immutable evidence for independent Gold producers and readers."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime

import pyarrow as pa
import pyarrow.parquet as pq

from .common import IntegrityError, atomic_json, canonical, digest, read_json, within
from .producer import assignment_tables

SCHEMA = "quality-independent-input-v1"


def save(root, document, tables):
    """Persist all bytes before making this input eligible for a Gold publication."""
    root.mkdir(parents=True, exist_ok=True)
    document, tables = dict(document), dict(tables)
    if "lineages" in document:
        proofs = [
            {"family": family, "key": key, "proof": json.dumps(proof, sort_keys=True)}
            for family, keys in document.pop("lineages").items()
            for key, records in keys.items()
            for proof in records
        ]
        tables["__lineage"] = pa.Table.from_pylist(
            proofs,
            schema=pa.schema(
                [("family", pa.string()), ("key", pa.string()), ("proof", pa.string())]
            ),
        )
    refs = []
    for name, table in sorted(tables.items()):
        sink = pa.BufferOutputStream()
        pq.write_table(table, sink)
        data = sink.getvalue().to_pybytes()
        identity = hashlib.sha256(data).hexdigest()
        uri = f"data/{identity}.parquet"
        path = root / uri
        path.parent.mkdir(exist_ok=True)
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != identity:
                raise IntegrityError("Immutable Gold evidence changed")
        else:
            from ingesta_delta_lake.association_cas import publish

            publish(path, data)
        refs.append({"name": name, "uri": uri, "hash": identity, "rows": table.num_rows})
    payload = {**document, "schema_id": SCHEMA, "tables": refs}
    identity = digest(payload)
    from .common import immutable_json

    immutable_json(root / f"{identity}.json", payload)
    atomic_json(root / "current.json", {"publication_id": identity})
    return Evidence.open(root, identity)


class Evidence:
    def __init__(self, root, identity, document):
        self.root, self.publication_id, self.document = root, identity, document
        self.manifest = {
            **document,
            "publication_id": identity,
            "created_at": document.get("confirmed_at"),
            "source_mode": "continuous",
            "tables": [{**r, "delta_version": 0} for r in document["tables"]],
        }
        self._tables = {}

    @classmethod
    def open(cls, root, identity=None):
        identity = identity or read_json(root / "current.json")["publication_id"]
        if (
            not isinstance(identity, str)
            or len(identity) != 64
            or any(c not in "0123456789abcdef" for c in identity)
        ):
            raise IntegrityError("Invalid Gold evidence identifier")
        document = read_json(root / f"{identity}.json")
        if document.get("schema_id") != SCHEMA or digest(document) != identity:
            raise IntegrityError("Gold evidence hash differs")
        return cls(root, identity, document)

    def table(self, name, *, max_rows):
        if name not in self._tables:
            ref = next((r for r in self.document["tables"] if r["name"] == name), None)
            if ref is None or ref["rows"] > max_rows:
                raise IntegrityError("Gold evidence exceeds its explicit row budget")
            path = within(self.root, ref["uri"])
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != ref["hash"]:
                    raise IntegrityError("Gold evidence bytes differ")
            table = pq.read_table(path)
            if table.num_rows != ref["rows"]:
                raise IntegrityError("Gold evidence count differs")
            self._tables[name] = table
        if self._tables[name].num_rows > max_rows:
            raise IntegrityError("Gold evidence exceeds its explicit row budget")
        return self._tables[name]

    def proofs(self, *, max_rows):
        result = {}
        for row in self.table("__lineage", max_rows=max_rows).to_pylist():
            result.setdefault(row["family"], {}).setdefault(row["key"], []).append(
                json.loads(row["proof"])
            )
        return result

    def batches(self, name, *, max_rows=None, expression=None, **_kwargs):
        max_rows = max_rows if max_rows is not None else self.document.get("row_budget")
        if type(max_rows) is not int or max_rows < 1:
            raise IntegrityError("Gold evidence needs an explicit row budget")
        table = self.table(name, max_rows=max_rows)
        if expression is not None:
            import pyarrow.dataset as ds

            table = ds.dataset(table).to_table(filter=expression)
        yield from table.to_batches()

    def rows(self, name, **kwargs):
        return [r for b in self.batches(name, **kwargs) for r in b.to_pylist()]

    def derived(self, sources, *, identity):
        """An in-memory source vector used only by a bounded, read-only query."""
        document = {**self.document, "tables": [{"name": n} for n in sources]}
        result = Evidence(self.root, identity, document)
        result._tables = sources
        return result


def registry(projections):
    """Only published decisions, ordered by their explicit revision, never arrival."""
    from ingesta_delta_lake.associations import validate_registry

    result = {}
    for publication in projections:
        current = publication["registry"]
        if digest(current) != publication["registry_hash"]:
            raise IntegrityError("Published attribution registry hash differs")
        for key, row in current.items():
            previous = result.get(key)
            if previous is None or previous["revision"] < row["revision"]:
                result[key] = row
            elif previous["revision"] == row["revision"] and previous != row:
                raise IntegrityError("Conflicting accepted attribution revisions")
    if validate_registry(result):
        raise IntegrityError("Published attribution registries conflict")
    return result


def attribution(document, tables):
    from ingesta_delta_lake import associations as native
    from ingesta_delta_lake.pipeline_rows import portable

    bundle = {
        **document["bundle"],
        "sources": {
            n: portable(t.to_pylist()) for n, t in tables.items() if n in native.SOURCE_KEYS
        },
    }
    decisions = document["registry"]
    scopes = {native.review_scope(c["payload"]) for c in document.get("association_cases", [])} - {
        None
    }
    ledger = native.assign(bundle, decisions, native.prepare_context(bundle), scopes)
    result = assignment_tables(
        document["plant"],
        ledger,
        decisions,
        bundle["sources"]["LISTA_AR"],
        bundle["sources"]["SIP_DEFECTO_AR"],
    )
    projected = list(native.project(decisions, document["plant"], document["lines"]).values())
    for row in projected:
        for field in ("vigente_desde", "vigente_hasta"):
            if row[field] is not None:
                row[field] = datetime.fromisoformat(row[field])
    result["ASIGNACION_PRODUCCION_RAMAL"] = pa.Table.from_pylist(
        projected,
        schema=native.SCHEMA,
    )
    return {
        name: value.read_all() if isinstance(value, pa.RecordBatchReader) else value
        for name, value in result.items()
    }


def combine(left, right, *, max_rows):
    """Reconcile raw accepted evidence, then reuse the single attribution implementation."""
    from ingesta_delta_lake.common import IntegrityError as NativeIntegrityError
    from ingesta_delta_lake.contracts import CONTRACTS
    from ingesta_delta_lake.pipeline_rows import business_key, reconcile, review_hash

    if left.document["plant"] != right.document["plant"] or (
        left.document["environment"] != right.document["environment"]
    ):
        raise IntegrityError("Gold stores belong to different environments")
    tables, conflicts, scanned = {}, [], 0
    proofs = {id(e): e.proofs(max_rows=max_rows) for e in (left, right)}
    proof_map = {}
    rejected = defaultdict(set)
    for evidence in (left, right):
        for family, hashes in evidence.document["rejections"].items():
            rejected[family].update(hashes)
    names = {r["name"] for evidence in (left, right) for r in evidence.document["tables"]}
    for name in sorted(names):
        if name in {
            "__lineage",
            "ASIGNACION_PRODUCCION_RAMAL",
            "observation_assignments",
            "zc_presences",
        }:
            continue
        groups, schema = defaultdict(list), None
        for evidence in (left, right):
            if name not in {r["name"] for r in evidence.document["tables"]}:
                continue
            table = evidence.table(name, max_rows=max_rows)
            schema = table.schema
            scanned += table.num_rows
            if scanned > max_rows:
                raise IntegrityError("Combined Gold evidence exceeds its explicit row budget")
            for row in table.to_pylist():
                if name in CONTRACTS:
                    if review_hash(name, row) in rejected[name]:
                        continue
                    key = business_key(name, row)
                    for proof in proofs[id(evidence)].get(name, {}).get(key, []):
                        groups[key].append({**proof, "row": row})
                else:
                    # Auxiliary records use their existing business closure rules below.
                    groups[canonical(row)].append(row)
        if name in CONTRACTS:
            accepted = []
            for key, values in groups.items():
                try:
                    row = reconcile(name, values)
                    if row is not None:
                        accepted.append(row)
                        proof_map.setdefault(name, {})[key] = [
                            {k: v for k, v in item.items() if k != "row"}
                            for item in values
                            if item["row"] == row
                        ]
                except NativeIntegrityError as exc:
                    conflicts.append({"family": name, "key": key, "reason": str(exc)})
            tables[name] = pa.Table.from_pylist(accepted, schema=schema)
        else:
            tables[name] = pa.Table.from_pylist([v[0] for v in groups.values()], schema=schema)
    if conflicts:
        raise IntegrityError(
            "Combined Gold sources require explicit review: " + canonical(conflicts).decode("utf-8")
        )
    from .producer import auxiliary

    # Consolidate the same accepted auxiliary keys with the producer's closure rules.
    initial = {name: table.to_pylist() for name, table in tables.items() if name not in CONTRACTS}
    # Validate before seeding: two differing rows for one key cannot be resolved by dict overwrite.
    for name, keys in {
        "production_parameters": ("origen_fenix", "id"),
        "gobs_dictionary": ("id_gotas",),
        "machine_gobs": ("id_maquina_fenix", "vigente_desde"),
        "hot_rejection_evidence": ("origen_fenix", "id"),
    }.items():
        seen = {}
        for row in initial.get(name, []):
            key = tuple(row[k] for k in keys)
            prior = seen.get(key)
            if prior is not None and prior != row:
                closure = "fecha_off" if name == "production_parameters" else "vigente_hasta"
                if {k: v for k, v in prior.items() if k != closure} != {
                    k: v for k, v in row.items() if k != closure
                } or (prior.get(closure) is not None and row.get(closure) is not None):
                    raise IntegrityError("Contradictory accepted auxiliary keys")
                if row.get(closure) is None:
                    continue
            seen[key] = row
        initial[name] = list(seen.values())
    tables.update(auxiliary(left.root, (), captures=(), initial=initial))
    doc = dict(left.document)
    doc["registry"] = registry(
        [
            {"registry": e.document["registry"], "registry_hash": digest(e.document["registry"])}
            for e in (left, right)
        ]
    )
    if left.document["lines"] != right.document["lines"]:
        raise IntegrityError("Published Gold line mappings differ")
    doc["association_cases"] = [
        *left.document["association_cases"],
        *right.document["association_cases"],
    ]
    composition = dict(right.document["bundle"]["catalog_composition"])
    for key, value in left.document["bundle"]["catalog_composition"].items():
        if key in composition and composition[key] != value:
            composition[key] = {**value, "coverage": "REVIEW"}
        else:
            composition[key] = value
    cases = {
        family: [
            *left.document["bundle"]["cases"].get(family, []),
            *right.document["bundle"]["cases"].get(family, []),
        ]
        for family in CONTRACTS
    }
    doc["bundle"] = {**doc["bundle"], "catalog_composition": composition, "cases": cases}
    doc["coverage"] = [*left.document["coverage"], *right.document["coverage"]]
    tables.update(attribution(doc, tables))
    result = left.derived(tables, identity=digest([left.publication_id, right.publication_id]))
    result.document = {**doc, "lineages": proof_map}
    result.manifest = {
        **doc,
        "publication_id": result.publication_id,
        "tables": [{"name": n, "delta_version": 0} for n in tables],
    }
    return result
