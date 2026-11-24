"""Synthetic legacy migration benchmark. Run in an isolated temporary workspace."""

import argparse
import ctypes
import ctypes.wintypes
import json
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter, process_time

import pyarrow as pa

from app.gold.evidence import OPERATIONS, Evidence, _fragment_bounds, _write_table
from app.gold.fragment_delta import LINEAGE_SCHEMA, migrate_lineage, partition


def peak_rss():
    if sys.platform != "win32":
        import resource

        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return value if sys.platform == "darwin" else value * 1024

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.wintypes.DWORD),
            ("faults", ctypes.wintypes.DWORD),
            *[
                (name, ctypes.c_size_t)
                for name in (
                    "peak_rss",
                    "rss",
                    "peak_pool",
                    "pool",
                    "peak_nonpaged",
                    "nonpaged",
                    "pagefile",
                    "peak_pagefile",
                )
            ],
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = ctypes.wintypes.HANDLE
    measure = ctypes.windll.psapi.GetProcessMemoryInfo
    measure.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(Counters), ctypes.wintypes.DWORD]
    if not measure(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise OSError("GetProcessMemoryInfo failed")
    return counters.peak_rss


def legacy(root, count, width):
    from ingesta_delta_lake.contracts import CONTRACTS
    from ingesta_delta_lake.pipeline_rows import business_key, review_hash

    family = "LISTA_RECH"
    schema = CONTRACTS[family].schema
    refs, lineage_refs = [], []
    start = datetime(2026, 10, 1)
    for offset in range(0, count, width):
        stamp = start + timedelta(minutes=10 * (offset // width))
        rows = [
            dict(
                FECHA=stamp,
                origen_fenix="AV21",
                MOLDE=3,
                LINEA=21,
                COD_MAQ_ZF=4,
                INSPECC=100,
                RECH=4,
                RECH_AUTO=1,
                CAVIDAD=str(cavity),
                FECHA_R=None,
            )
            for cavity in range(min(width, count - offset))
        ]
        table = pa.Table.from_pylist(rows, schema=schema)
        refs.append(
            dict(
                _write_table(root, table),
                partition=list(partition(family, rows[0], 600)),
                time_bounds=_fragment_bounds(family, table),
            )
        )
        proofs = [
            dict(
                family=family,
                key=business_key(family, row),
                proof=json.dumps(
                    {
                        "source_table": "silver",
                        "source_version": offset // width,
                        "row_hash": review_hash(family, row),
                    },
                    sort_keys=True,
                ),
            )
            for row in rows
        ]
        lineage_refs.append(
            dict(
                _write_table(root, pa.Table.from_pylist(proofs, schema=LINEAGE_SCHEMA)),
                partition=[family, "silver", offset // width],
            )
        )
    tables = [
        dict(name=key, rows=count if key == family else 0, parts=refs if key == family else [])
        for key in sorted(OPERATIONS)
    ]
    tables.append(dict(name="__lineage", rows=count, parts=lineage_refs))
    return Evidence(root, "synthetic-legacy", dict(row_budget=50_000, tables=tables))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", required=True, type=int)
    parser.add_argument("--rows-per-slot", type=int, default=1024)
    args = parser.parse_args()
    if min(args.rows, args.rows_per_slot) < 1:
        parser.error("Positive row counts are required")
    with TemporaryDirectory(prefix="fenix-gold-benchmark-") as directory:
        root = Path(directory)
        previous = legacy(root / "inputs", args.rows, args.rows_per_slot)
        print(
            json.dumps({"phase": "fixture_ready", "rows": args.rows, "peak_rss": peak_rss()}),
            flush=True,
        )
        stop = threading.Event()
        peak = [0]

        def disk():
            while not stop.wait(0.05):
                sizes = []
                for path in (root / "checkpoint").glob("lineage-migration-*/*"):
                    try:
                        sizes.append(path.stat().st_size)
                    except FileNotFoundError:
                        continue
                peak[0] = max(peak[0], sum(sizes))

        monitor = threading.Thread(target=disk, daemon=True)
        wall, cpu = perf_counter(), process_time()
        monitor.start()
        try:
            result = migrate_lineage(previous, 600, batch_size=4096)
        finally:
            stop.set()
            monitor.join()
        lineage = next(ref for ref in result.document["tables"] if ref["name"] == "__lineage")
        assert lineage["rows"] == args.rows
        print(
            json.dumps(
                dict(
                    phase="migration",
                    rows=args.rows,
                    wall_seconds=perf_counter() - wall,
                    cpu_seconds=process_time() - cpu,
                    process_peak_rss_bytes=peak_rss(),
                    sampled_peak_temporary_bytes=peak[0],
                    lineage_partitions=len(lineage["parts"]),
                )
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
