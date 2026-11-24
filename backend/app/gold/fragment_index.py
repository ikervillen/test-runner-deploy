"""Persistent index for immutable Gold fragments, ordered by the Silver time slot."""

from collections import OrderedDict, defaultdict

from .common import IntegrityError, digest, immutable_json, read_json, within

DEPTH = 16
PAGE_LIMIT = 256_000
SLOT_LIMIT = 64


def _key(partition):
    slot = partition[-1] + (1 << 63)
    if not 0 <= slot < 1 << 64:
        raise IntegrityError("Gold fragment slot is outside the index")
    return f"{slot:016x}"


def _ref(path_hash, children):
    ref = {"uri": f"index/tree/{path_hash}.json", "hash": path_hash}
    ref["rows"] = sum(child["rows"] for child in children)
    slots = [
        [child["partition"][-1]] * 2 if "partition" in child else child["slots"]
        for child in children
    ]
    ref["slots"] = [min(bounds[0] for bounds in slots), max(bounds[1] for bounds in slots)]
    bounds = [child.get("time_bounds") for child in children]
    if all(bounds):
        ref["time_bounds"] = [
            min(bound[0] for bound in bounds),
            max(bound[1] for bound in bounds)
            if all(bound[1] is not None for bound in bounds)
            else None,
        ]
    return ref


class FragmentIndex:
    def __init__(self, root):
        self.root = root
        self.pages_read = 0
        self.pages_written = 0
        self._cache = OrderedDict()

    def _remember(self, ref, node):
        self._cache[ref["hash"]] = (ref, node)
        self._cache.move_to_end(ref["hash"])
        if len(self._cache) > 512:
            self._cache.popitem(last=False)

    def page(self, ref):
        identity = ref["hash"]
        if identity not in self._cache:
            node = read_json(within(self.root, ref["uri"]), max_bytes=PAGE_LIMIT)
            children = node.get("children")
            parts = node.get("parts")
            if digest(node) != identity or (children is None) == (parts is None):
                raise IntegrityError("Gold fragment index page differs")
            values = list(children.values()) if children is not None else parts
            if not values or _ref(identity, values) != ref:
                raise IntegrityError("Gold fragment index summary differs")
            if children is not None and any(
                len(key) != 1 or key not in "0123456789abcdef" for key in children
            ):
                raise IntegrityError("Gold fragment index child key differs")
            if parts is not None and len(parts) > SLOT_LIMIT:
                raise IntegrityError("Gold fragment slot exceeds its descriptor budget")
            self._remember(ref, node)
            self.pages_read += 1
        expected, node = self._cache[identity]
        if ref != expected:
            raise IntegrityError("Gold fragment index summary differs")
        self._cache.move_to_end(identity)
        return node

    def _write(self, node, values):
        identity = digest(node)
        ref = _ref(identity, values)
        path = within(self.root, ref["uri"])
        existed = path.exists()
        immutable_json(path, node, max_bytes=PAGE_LIMIT)
        self._remember(ref, node)
        self.pages_written += not existed
        return ref

    def updated(self, root, edits):
        """Copy only paths affected by partition upserts/deletions."""
        keyed = {_key(partition): {} for partition in edits}
        for partition, value in edits.items():
            keyed[_key(partition)][partition] = value

        def visit(ref, depth, changes):
            if not changes:
                return ref
            if depth == DEPTH:
                current = (
                    {tuple(part["partition"]): part for part in self.page(ref)["parts"]}
                    if ref
                    else {}
                )
                for group in changes.values():
                    for partition, value in group.items():
                        if value is None:
                            current.pop(partition, None)
                        else:
                            current[partition] = value
                if not current:
                    return None
                if len(current) > SLOT_LIMIT:
                    raise IntegrityError("Gold fragment slot exceeds its descriptor budget")
                values = [current[key] for key in sorted(current)]
                return self._write({"parts": values}, values)
            children = dict(self.page(ref)["children"]) if ref else {}
            grouped = defaultdict(dict)
            for key, change in changes.items():
                grouped[key[depth]][key] = change
            for digit, change in grouped.items():
                child = visit(children.get(digit), depth + 1, change)
                if child is None:
                    children.pop(digit, None)
                else:
                    children[digit] = child
            return (
                self._write({"children": children}, list(children.values())) if children else None
            )

        return visit(root, 0, keyed)

    def find(self, root, partition):
        ref = root
        for digit in _key(partition):
            if ref is None:
                return None
            ref = self.page(ref)["children"].get(digit)
        if ref:
            return next(
                (part for part in self.page(ref)["parts"] if tuple(part["partition"]) == partition),
                None,
            )
        return None

    def parts(self, root, accept=None):
        if root is None or (accept and not accept(root)):
            return
        node = self.page(root)
        for part in node.get("parts", []):
            if not accept or accept(part):
                yield part
        for ref in node.get("children", {}).values():
            yield from self.parts(ref, accept)
