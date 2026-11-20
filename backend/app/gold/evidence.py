"""Self-contained, immutable evidence for independent Gold producers and readers."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .common import IntegrityError, atomic_json, canonical, digest, read_json, within
from .producer import assignment_tables

SCHEMA = "quality-independent-input-v1"


OPERATIONS = {"LISTA_DEFECTOS", "LISTA_RECH", "CONT_ZC", "LISTA_AR"}


def evidence_files(ref):
    """Backward compatible with the original, single-Parquet manifest entries."""
    return ref.get("parts", [ref])


def checked_file(root, ref):
    path = within(root, ref["uri"])
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != ref["hash"]:
            raise IntegrityError("Gold evidence bytes differ")
    return path


def _write_table(root, table):
    from ingesta_delta_lake.association_cas import publish

    sink = pa.BufferOutputStream()
    pq.write_table(table, sink)
    data = sink.getvalue().to_pybytes()
    identity = hashlib.sha256(data).hexdigest()
    ref = {"uri": f"data/{identity}.parquet", "hash": identity, "rows": table.num_rows}
    path = within(root, ref["uri"])
    if path.exists():
        checked_file(root, ref)
    else:
        publish(path, data)
    return ref


def _fragments(name, table, window_seconds):
    if not window_seconds or not table.num_rows:
        return None
    groups = defaultdict(list)
    if name in OPERATIONS:
        epoch = datetime(1970, 1, 1)
        field = "FECHA_ON" if name == "LISTA_AR" else "FECHA"
        for i, (origin, stamp) in enumerate(zip(table["origen_fenix"], table[field], strict=True)):
            offset = (stamp.as_py() - epoch) // timedelta(seconds=window_seconds)
            groups[origin.as_py(), offset].append(i)
    elif name == "__lineage":
        for i, (family, key, proof) in enumerate(
            zip(
                table["family"],
                table["key"],
                table["proof"],
                strict=True,
            )
        ):
            parsed = json.loads(proof.as_py())
            if family.as_py() in OPERATIONS:
                part = parsed["source_table"], parsed["source_version"]
            else:
                part = "catalog", 0
            groups[family.as_py(), *part].append((key.as_py(), proof.as_py(), i))
        return [(key, table.take([i for _, _, i in sorted(groups[key])])) for key in sorted(groups)]
    else:
        return None
    return [(key, table.take(groups[key])) for key in sorted(groups)]


def _fragment_bounds(name, table):
    field = "FECHA_ON" if name == "LISTA_AR" else "FECHA"
    bounds = pc.min_max(table[field]).as_py()
    if name == "LISTA_AR":
        ends = table["FECHA_OFF"]
        return [
            bounds["min"].isoformat(),
            None if ends.null_count else pc.max(ends).as_py().isoformat(),
        ]
    return [bounds["min"].isoformat(), bounds["max"].isoformat()]


def _overlaps_fragment(name, item, window):
    bounds = item.get("time_bounds")
    if bounds is None:
        return True  # Older evidence has no signed time bounds.
    first, last = [datetime.fromisoformat(value) if value is not None else None for value in bounds]
    left, right = window
    if name == "LISTA_AR":
        return first < right and (last is None or last > left)
    return first <= right and last > left


def save(root, document, tables, *, window_seconds=None, reuse=None):
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
        fragments = _fragments(name, table, window_seconds)
        if not fragments:
            ref = reuse.matching_file(name, table) if reuse else None
            if ref is None:
                ref = _write_table(root, table)
        else:
            previous = reuse.fragments(name) if reuse else {}
            descriptors = []
            for key, part in fragments:
                old = previous.get(tuple(key))
                if old and old[1].equals(part):
                    item = dict(old[0])
                else:
                    item = {**_write_table(root, part), "partition": list(key)}
                if name in OPERATIONS and "time_bounds" not in item:
                    item["time_bounds"] = _fragment_bounds(name, part)
                descriptors.append(item)
            ref = {"parts": descriptors, "rows": table.num_rows}
        refs.append({"name": name, **ref})
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
        self._fragments = {}
        self.read_stats = defaultdict(Counter)

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
            parts = []
            for item in evidence_files(ref):
                parts.append(self._read_part(name, item))
            table = pa.concat_tables(parts) if len(parts) > 1 else parts[0]
            if table.num_rows != ref["rows"]:
                raise IntegrityError("Gold evidence count differs")
            self._tables[name] = table
        if self._tables[name].num_rows > max_rows:
            raise IntegrityError("Gold evidence exceeds its explicit row budget")
        return self._tables[name]

    def _read_part(self, name, item):
        partition = tuple(item.get("partition", ()))
        parts = self._fragments.setdefault(name, {}) if partition else {}
        previous = parts.get(partition)
        if previous and previous[0] == item:
            self.read_stats[name]["fragments_reused"] += 1
            return previous[1]
        table = pq.ParquetFile(checked_file(self.root, item)).read(use_threads=False)
        if table.num_rows != item["rows"]:
            raise IntegrityError("Gold evidence fragment count differs")
        self.read_stats[name].update(fragments_opened=1, rows_decoded=table.num_rows)
        if partition:
            parts[partition] = (item, table)
        return table

    def fragments(self, name):
        return self._fragments.get(name, {})

    def matching_file(self, name, table):
        previous = self._tables.get(name)
        if previous is not None and previous.equals(table):
            ref = next(r for r in self.document["tables"] if r["name"] == name)
            if "uri" in ref:
                return {key: ref[key] for key in ("uri", "hash", "rows")}
        return None

    def proofs(self, *, max_rows):
        result = {}
        for row in self.table("__lineage", max_rows=max_rows).to_pylist():
            result.setdefault(row["family"], {}).setdefault(row["key"], []).append(
                json.loads(row["proof"])
            )
        return result

    def batches(self, name, *, max_rows=None, expression=None, window=None, **_kwargs):
        max_rows = max_rows if max_rows is not None else self.document.get("row_budget")
        if type(max_rows) is not int or max_rows < 1:
            raise IntegrityError("Gold evidence needs an explicit row budget")
        ref = next((r for r in self.document["tables"] if r["name"] == name), None)
        if window and name in OPERATIONS and ref and "parts" in ref and name not in self._tables:
            scanned = 0
            for part in ref["parts"]:
                if not _overlaps_fragment(name, part, window):
                    self.read_stats[name]["fragments_pruned"] += 1
                    continue
                scanned += part["rows"]
                if scanned > max_rows:
                    raise IntegrityError("Gold evidence exceeds its explicit row budget")
                table = self._read_part(name, part)
                rule = (
                    expression if expression is not None else operational_expression(name, window)
                )
                yield from table.filter(rule).to_batches()
            return
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


def operational_expression(family, window):
    import pyarrow.dataset as ds

    if window is None:
        return None
    left, right = window
    if family == "LISTA_AR":
        return (ds.field("FECHA_ON") < right) & (
            ds.field("FECHA_OFF").is_null() | (ds.field("FECHA_OFF") > left)
        )
    return (ds.field("FECHA") > left) & (ds.field("FECHA") <= right)


def attribution(document, tables, *, window=None):
    from ingesta_delta_lake import associations as native
    from ingesta_delta_lake.pipeline_rows import portable

    sources = {}
    for name in native.SOURCE_KEYS:
        if name in tables:
            table = tables[name]
            if window and name in ("LISTA_DEFECTOS", "LISTA_RECH", "CONT_ZC", "LISTA_AR"):
                table = table.filter(operational_expression(name, window))
            sources[name] = portable(table.to_pylist())
    bundle = {**document["bundle"], "sources": sources}
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
    from ingesta_delta_lake.pipeline_rows import (
        business_key,
        conflicting_keys,
        reconcile,
        review_hash,
    )

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
            invalid = conflicting_keys(name, {business_key(name, row): row for row in accepted})
            conflicts.extend(
                {"family": name, "key": key, "reason": "CROSS_LANE_CONTRACT_CONFLICT"}
                for key in sorted(invalid)
            )
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
    doc["coverage"] = combined_coverage([*left.document["coverage"], *right.document["coverage"]])
    tables.update(attribution(doc, tables))
    result = left.derived(tables, identity=digest([left.publication_id, right.publication_id]))
    result.document = {**doc, "lineages": proof_map}
    result.manifest = {
        **doc,
        "publication_id": result.publication_id,
        "tables": [{"name": n, "delta_version": 0} for n in tables],
    }
    return result


def combined_coverage(rows):
    """Join accredited adjacent intervals; never bridge an unobserved gap."""
    groups = defaultdict(list)
    for row in rows:
        groups[(row["source"], row.get("origin"), row.get("id_produccion"))].append(row)
    output = []
    for values in groups.values():
        ready = [dict(r) for r in values if r["estado"] == "READY" and not r.get("reason_code")]
        output.extend(
            r
            for r in values
            if r["estado"] != "READY"
            and (r.get("reason_code") or r["estado"] != "WAITING_INPUT" or not ready)
        )
        output.extend(r for r in values if r["estado"] == "READY" and r.get("reason_code"))
        merged = []
        for row in sorted(ready, key=lambda r: r.get("covered_from") or ""):
            if merged and (
                merged[-1].get("covered_until") is None
                or row.get("covered_from") is None
                or row["covered_from"] <= merged[-1]["covered_until"]
            ):
                before = merged[-1]
                ends = [before.get("covered_until"), row.get("covered_until")]
                before["covered_until"] = max(ends) if all(ends) else None
                for key in ("live_until", "last_successful_discovery"):
                    available = [r[key] for r in (before, row) if r.get(key)]
                    if available:
                        before[key] = max(available)
            else:
                merged.append(row)
        output.extend(merged)
    return output
