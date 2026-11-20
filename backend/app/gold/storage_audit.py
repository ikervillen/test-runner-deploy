"""Read-only audit of Gold file allocation and Delta publication pins."""

import argparse
import json
import os
import stat
import sys
from bisect import bisect_left
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import unquote

from deltalake import DeltaTable

from .common import IntegrityError, canonical, within
from .evidence import checked_file
from .evidence_storage import OBJECT
from .publications import Snapshot, envelope


def _historical_files(directory, pins):
    """Resolve the union of historical pins with one pass over Delta metadata."""
    if not pins:
        return set()
    active, referenced = {}, set()
    for version in range(pins[-1] + 1):
        log = directory / "_delta_log" / f"{version:020d}.json"
        if not log.is_file():
            return None  # No claim about historical reachability if there is a log gap.
        adds, removes = [], []
        with log.open(encoding="utf-8") as stream:
            for line in stream:
                action = json.loads(line)
                if "add" in action:
                    adds.append(unquote(action["add"]["path"]))
                if "remove" in action:
                    removes.append(unquote(action["remove"]["path"]))
        for file in removes:
            first = active.pop(file, None)
            if first is not None and pins[bisect_left(pins, first)] < version:
                referenced.add(directory / file)
        active.update((file, version) for file in adds)
    referenced.update(directory / file for file in active)
    return referenced


def measure(root, *, previous=None, verify_evidence=False):
    published = root / "published"
    _, manifest = envelope(published)
    latest = manifest["publication_id"]
    snapshot = Snapshot(published, manifest)
    active = {
        Path(path).resolve()
        for ref in manifest["tables"]
        for path in snapshot.table(ref["name"]).file_uris()
    }
    pins = defaultdict(set)
    parent = manifest["previous_publication_id"]
    visited = {latest}
    while parent:
        if parent in visited:
            raise IntegrityError("Cycle in Gold publication history")
        visited.add(parent)
        _, record = envelope(published, parent)
        for ref in record["tables"]:
            pins[ref["uri"]].add(ref["delta_version"])
        parent = record["previous_publication_id"]
    historical, gaps = set(), []
    for uri, versions in sorted(pins.items()):
        files = _historical_files(within(published, uri), sorted(versions))
        if files is None:
            gaps.append(uri)
        else:
            historical.update(files)
    inodes, paths, groups = {}, {}, defaultdict(Counter)
    unknown_blocks = 0
    order = ("active", "historical_pinned", "outside_pin", "evidence", "other")
    rank = {name: index for index, name in enumerate(order)}
    for folder, _, names in os.walk(root):
        for name in names:
            file = Path(folder) / name
            try:
                info = file.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            relative = file.relative_to(root).as_posix()
            group = "other"
            if file in active:
                group = "active"
            elif file in historical:
                group = "historical_pinned"
            elif relative.startswith("published/tables/") and file.suffix == ".parquet":
                if "_delta_log" not in file.parts and "_change_data" not in file.parts:
                    group = "outside_pin"
            elif relative.startswith(("inputs/data/", "published/evidence/data/")):
                group = "evidence"
            identity = f"{info.st_dev}:{info.st_ino}"
            size = info.st_blocks * 512 if hasattr(info, "st_blocks") else None
            groups[group].update(paths=1, logical_bytes=info.st_size)
            if identity not in inodes or rank[group] < rank[inodes[identity][0]]:
                inodes[identity] = (group, size, info.st_nlink)
            if group == "evidence":
                paths[relative] = (identity, info.st_size, size)
    for group, size, links in inodes.values():
        groups[group]["unique_inodes"] += 1
        groups[group]["inodes_with_hardlinks"] += links > 1
        if size is None:
            unknown_blocks += 1
            groups[group]["unmeasurable_inodes"] += 1
        else:
            groups[group]["allocated_unique_bytes"] += size
    evidence = Counter()
    for path, (inode, size, _) in paths.items():
        if not path.startswith("inputs/data/"):
            continue
        name = Path(path).name
        match = OBJECT.fullmatch(name)
        other = paths.get(f"published/evidence/data/{name}")
        if match is None or other is None or size != other[1]:
            continue
        if inode == other[0]:
            evidence["hardlinked_pairs"] += 1
        else:
            if verify_evidence:
                ref = {"uri": f"data/{name}", "hash": match[1]}
                checked_file(root / "inputs", ref)
                checked_file(published / "evidence", ref)
            evidence["separate_inode_pairs"] += 1
            if other[2] is not None and inodes[other[0]][2] == 1:
                evidence["destination_allocated_bytes"] += other[2]
    device = root.stat().st_dev
    report = {
        "schema": 1,
        "root": str(root),
        "device": device,
        "root_inode": root.stat().st_ino,
        "pin": latest,
        "pin_unchanged_during_sample": envelope(published)[1]["publication_id"] == latest,
        "historical_publications": len(visited) - 1,
        "historical_log_gaps": gaps,
        "groups": dict(groups),
        "evidence": {**evidence, "verified_hashes": verify_evidence},
        "allocated_unique_bytes": None if unknown_blocks else sum(v[1] for v in inodes.values()),
        "inode_blocks": {key: row[1] for key, row in inodes.items()},
    }
    if previous is not None:
        identity = ("schema", "root", "device", "root_inode")
        if any(previous.get(key) != report[key] for key in identity):
            raise IntegrityError("Previous inventory belongs to a different volume")
        before, after = previous["inode_blocks"], report["inode_blocks"]
        if None not in before.values() and None not in after.values():
            report["change"] = {
                "new_inode_allocated_bytes": sum(
                    after[key] for key in after.keys() - before.keys()
                ),
                "allocation_growth_bytes": sum(
                    max(0, v - before.get(k, 0)) for k, v in after.items()
                ),
                "net_allocated_bytes": sum(after.values()) - sum(before.values()),
            }
    return report


def scope_diff(root, before, after, *, budget):
    """Compare two versions explicitly, off the writer path."""
    path = root / "published/tables/publication_scope_status"
    prior = {}
    checked = 0
    counts = dict(inserted=0, updated=0, unchanged=0)
    for version, baseline in ((before, True), (after, False)):
        scanner = DeltaTable(path, version=version).to_pyarrow_dataset().scanner(use_threads=False)
        for batch in scanner.to_batches():
            checked += batch.num_rows
            if checked > budget:
                raise IntegrityError("Scope diff exceeds its row budget")
            for row in batch.to_pylist():
                key = row["scope_id"]
                value = canonical(row)
                if baseline:
                    prior[key] = value
                else:
                    previous = prior.pop(key, None)
                    if previous is None:
                        counts["inserted"] += 1
                    elif previous != value:
                        counts["updated"] += 1
                    else:
                        counts["unchanged"] += 1
    return {"before": before, "after": after, **counts, "deleted": len(prior)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--previous-stdin", action="store_true")
    parser.add_argument("--verify-evidence", action="store_true")
    parser.add_argument("--scope-diff", nargs=2, type=int, metavar=("BEFORE", "AFTER"))
    parser.add_argument("--row-budget", type=int)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    if args.scope_diff is not None:
        if not args.row_budget or args.row_budget < 1 or min(args.scope_diff) < 0:
            parser.error("scope diff needs nonnegative versions and an explicit positive budget")
        print(json.dumps(scope_diff(root, *args.scope_diff, budget=args.row_budget)))
        return
    previous = json.load(sys.stdin) if args.previous_stdin else None
    print(
        json.dumps(
            measure(
                root,
                previous=previous,
                verify_evidence=args.verify_evidence,
            ),
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
