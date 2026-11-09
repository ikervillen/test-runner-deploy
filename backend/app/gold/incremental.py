"""Index accepted facts by production/period; reuse bases only for identical raw inputs."""

from bisect import bisect_right
from collections import defaultdict

from .common import digest
from .metrics import mia_bases


def group(rows, key):
    result = defaultdict(list)
    for row in rows:
        result[row[key]].append(row)
    return result


def window_key(row):
    return tuple(
        row[k] for k in ("id_produccion", "tipo_ventana", "inicio", "fin", "corte", "seleccion_id")
    )


class PeriodIndex:
    def __init__(self, rows):
        self.rows = sorted(rows, key=lambda r: r["periodo_fin"])
        self.ends = [r["periodo_fin"] for r in self.rows]

    def select(self, window):
        left = bisect_right(self.ends, window.start)
        right = bisect_right(self.ends, window.cutoff)
        return [r for r in self.rows[left:right] if window.includes_sample(r)]


class Prepared:
    def __init__(self, productions, tables, capacities, zc_links, previous_states, old_tables):
        self.rejections = {
            k: PeriodIndex(v) for k, v in group(tables["mia_rejections"], "id_produccion").items()
        }
        self.inspections = {
            k: PeriodIndex(v) for k, v in group(tables["mia_inspections"], "id_produccion").items()
        }
        ar = group(tables["ar_intervals"], "id_produccion")
        cap = group(capacities, "id_produccion")
        zc = {r["id_observacion"]: r for r in tables["zc_observations"]}
        self.raw_hashes = {}
        self.unchanged = set()
        for p in productions:
            identity = p["id_produccion"]
            # Preserve the established hash ordering, independent of the period index.
            fingerprint = digest(
                [
                    p,
                    sorted(
                        self.rejections.get(identity, PeriodIndex([])).rows,
                        key=lambda r: r["id_observacion"],
                    ),
                    sorted(
                        self.inspections.get(identity, PeriodIndex([])).rows,
                        key=lambda r: r["id_observacion"],
                    ),
                    [
                        {k: v for k, v in r.items() if k not in {"categoria", "grupo_dar"}}
                        for r in ar[identity]
                    ],
                    cap[identity],
                    [zc[k] for k in sorted(zc_links[identity]) if k in zc],
                ]
            )
            self.raw_hashes[identity] = fingerprint
            if previous_states.get(identity, {}).get("raw_inputs_hash") == fingerprint:
                self.unchanged.add(identity)
        self.old_exposures = {window_key(r): r for r in old_tables.get("mia_window_exposures", [])}
        self.old_amounts = group(old_tables.get("mia_window_bases", []), "inspected_basis_id")
        self.old_bases = group(old_tables.get("inspection_bases", []), "id_produccion")
        self.stats = {
            "mia_windows_reused": 0,
            "mia_windows_recalculated": 0,
            "unchanged_productions": len(self.unchanged),
            "productions_by_origin": dict(
                (origin, sum(p["origen_fenix"] == origin for p in productions))
                for origin in sorted({p["origen_fenix"] for p in productions if p["origen_fenix"]})
            ),
        }

    def mia(self, window):
        old = self.old_exposures.get(window_key(window.key))
        if window.production_id in self.unchanged and old is not None:
            self.stats["mia_windows_reused"] += 1
            amounts = self.old_amounts.get(old["inspected_basis_id"], [])
            return {
                "exposure": dict(old),
                "amounts": [dict(r) for r in amounts],
                "inspection_bases": [
                    dict(r)
                    for r in self.old_bases[window.production_id]
                    if window.includes_sample(r) and r["seleccion_id"] == window.selection_id
                ],
                "rates": [
                    dict(
                        espacio_defecto=r["espacio_defecto"],
                        codigo_defecto=r["codigo_defecto"],
                        rechazos=r["rechazos"],
                        inspeccionadas=old["inspeccionadas"],
                        percent=100 * r["rechazos"] / old["inspeccionadas"]
                        if old["inspeccionadas"]
                        else None,
                    )
                    for r in amounts
                    if r["rechazos"] > 0
                ],
            }
        self.stats["mia_windows_recalculated"] += 1
        return mia_bases(
            self.rejections.get(window.production_id, PeriodIndex([])).select(window),
            self.inspections.get(window.production_id, PeriodIndex([])).select(window),
            window,
        )
