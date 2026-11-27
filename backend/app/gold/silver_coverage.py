"""Coverage certificates for confirmed ten-minute Silver slots."""

from collections import defaultdict
from datetime import datetime, timedelta

from .common import atomic_json, read_json
from .evidence import OPERATIONS
from .fragment_delta import EPOCH

SLOT_FAMILIES = OPERATIONS - {"LISTA_AR"}
COVERAGE_RULE = "confirmed-slots-v4"


def slot_receipts(snapshot, family):
    manifest = snapshot.manifest
    ref = manifest["tables"].get(family) or manifest.get("controls", {}).get(family, {})
    return ref.get("slot_receipts", ())


def slot_number(stamp, seconds):
    return (stamp - EPOCH) // timedelta(seconds=seconds)


def required_slots(start, end, seconds):
    step = timedelta(seconds=seconds)
    last = slot_number(end + step - timedelta(microseconds=1), seconds)
    return range(slot_number(start, seconds) + 1, last + 1)


def covers(row, window):
    if "confirmed_slots" not in row:
        return None
    known = set(row["confirmed_slots"])
    required = required_slots(window.start, window.cutoff, row["slot_seconds"])
    return bool(required) and all(slot in known for slot in required)


def confirmed_blanks(storage, snapshot, policy, *, receipts=None, after=None, through=None):
    """Verify exact Bronze bytes against owner outcomes carried by the Silver pin."""
    from ingesta_delta_lake.common import blank_input
    from ingesta_delta_lake.pipeline_bronze import BronzeFeed

    try:
        feed = BronzeFeed(storage)
    except FileNotFoundError:
        return {}, {}
    if feed.identity != snapshot.manifest["identity"] or not feed.sequence:
        return {}, {}
    latest, accepted, captures = {}, {}, {}
    for family in SLOT_FAMILIES:
        for seq, identity, origin, stamp, confirmed in slot_receipts(snapshot, family):
            key = family, origin, slot_number(datetime.fromisoformat(stamp), policy.window_seconds)
            latest[key] = seq, identity
            if confirmed:
                accepted[key] = seq, identity, stamp

    # Bronze may already contain a newer, still unconfirmed revision.
    through = min(feed.sequence, through) if through is not None else feed.sequence
    if after is None:
        after = max(0, through - min(512, max(256, policy.live_batch_files)))
    changes = feed.captures(after=after, limit=through - after) if through > after else ()
    for captured in changes:
        item = captured.snapshot
        if captured.family not in SLOT_FAMILIES or "T" not in (item.producer_time or ""):
            continue
        slot = slot_number(datetime.fromisoformat(item.producer_time), policy.window_seconds)
        key = captured.family, item.origin, slot
        if key not in latest or latest[key][0] <= captured.sequence:
            latest[key] = captured.sequence, captured.input_id
            captures[key] = captured
    verified = {}
    for key, (seq, identity, stamp) in accepted.items():
        if latest[key] != (seq, identity) or seq > through:
            continue
        if receipts is not None:
            prior = receipts.entry(*key)
            if prior and (prior[0] > seq or (prior[:2] == [seq, identity] and prior[3])):
                continue
        captured = captures.get(key)
        try:
            if captured is None:
                captured = next(feed.captures(after=seq - 1, limit=1), None)
            if (
                captured
                and captured.input_id == identity
                and captured.family == key[0]
                and captured.snapshot.origin == key[1]
                and captured.snapshot.producer_time == stamp
                and captured.snapshot.size <= 4096
            ):
                verified[key] = (
                    identity if blank_input(feed.read(captured, max_file_bytes=4096)) else None
                )
        except FileNotFoundError:
            continue
    return latest, verified


class BlankReceipts:
    """Compact per-day index of verified empty captures, outside the worker checkpoint."""

    def __init__(self, root, seconds, *, persist=True):
        self.root, self.seconds = root, seconds
        self.persist = persist
        self.pages = {}

    def page(self, slot):
        day = (EPOCH + timedelta(seconds=self.seconds * slot)).date().isoformat()
        if day not in self.pages:
            path = self.root / f"{day}.json"
            self.pages[day] = (path, read_json(path) if self.persist and path.is_file() else {})
        return self.pages[day]

    def confirmed(self, family, origin, slot):
        certificate = self.entry(family, origin, slot)
        return bool(certificate and certificate[2])

    def entry(self, family, origin, slot):
        _, page = self.page(slot)
        return page.get(f"{family}:{origin}:{slot}")

    def update(self, seen, verified):
        edits = {}
        for (family, origin, slot), (sequence, newest) in seen.items():
            path, page = self.page(slot)
            key = f"{family}:{origin}:{slot}"
            prior = page.get(key)
            capture = verified.get((family, origin, slot)) == newest
            if prior and (prior[0] > sequence or (prior[:2] == [sequence, newest] and prior[3])):
                continue
            value = [sequence, newest, capture, (family, origin, slot) in verified]
            if prior == value:
                continue
            page[key] = value
            edits[path] = page
        for path, page in edits.items() if self.persist else ():
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(path, page)


class SlotCoverage:
    def __init__(self, coverage, *, seconds, indexed, tables, index, receipts):
        self.seconds = seconds
        self.index = index
        self.roots = {}
        self.slots = defaultdict(set)
        self.examined = set()
        self.coverage = {
            (row["source"], row["origin"]): row
            for row in coverage
            if row["source"] in SLOT_FAMILIES and row["estado"] == "READY"
        }
        self.receipts = receipts
        for name in SLOT_FAMILIES:
            if name in indexed:
                ref = indexed[name][0]
                if ref.get("parts_root"):
                    self.roots[name] = ref["parts_root"]
                for part in ref.get("parts", ()):
                    origin = part["partition"][0]
                    if part.get("time_bounds") and part["rows"]:
                        self.slots[name, origin].add(part["partition"][-1])
            elif name in tables:
                table = tables[name]
                for batch in table.select(["origen_fenix", "FECHA"]).to_batches():
                    for row in batch.to_pylist():
                        self.slots[name, row["origen_fenix"]].add(
                            slot_number(row["FECHA"], seconds)
                        )

    def prepare(self, start, cutoff, *, origin=None):
        slots = tuple(required_slots(start, cutoff, self.seconds))
        for (family, line), row in self.coverage.items():
            if origin is not None and line != origin:
                continue
            known = set(row.get("confirmed_slots", ()))
            for slot in slots:
                key = family, line, slot
                if key in self.examined:
                    continue
                self.examined.add(key)
                if (
                    slot in self.slots[family, line]
                    or self.receipts.confirmed(family, line, slot)
                    or (family in self.roots and self.index.find(self.roots[family], (line, slot)))
                ):
                    known.add(slot)
            row["confirmed_slots"] = sorted(known)
            row["slot_seconds"] = self.seconds

    def finalize(self, horizon):
        block = slot_number(horizon, self.seconds)
        step = timedelta(seconds=self.seconds)
        for row in self.coverage.values():
            row.pop("covered_from", None)
            row.pop("covered_until", None)
            known = set(row.get("confirmed_slots", ()))
            recent = [slot for slot in known if slot <= block]
            if recent:
                right = left = max(recent)
                while left - 1 in known:
                    left -= 1
                row["covered_from"] = (EPOCH + step * (left - 1)).isoformat()
                row["covered_until"] = (EPOCH + step * right).isoformat()
