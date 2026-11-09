"""Parse accepted association artifacts with bounded buffers, including CAS blocks."""

import gzip
import io
from contextlib import contextmanager

import ijson

from .common import IntegrityError


class Blocks(io.RawIOBase):
    def __init__(self, blocks):
        self.blocks = iter(blocks)
        self.pending = memoryview(b"")

    def readable(self):
        return True

    def readinto(self, target):
        if not self.pending:
            self.pending = memoryview(next(self.blocks, b""))
        count = min(len(target), len(self.pending))
        target[:count] = self.pending[:count]
        self.pending = self.pending[count:]
        return count


@contextmanager
def stream(config, descriptor):
    from ingesta_delta_lake.association_cas import FORMAT, read_blocks, resolve

    descriptor = resolve(config, descriptor)
    if descriptor.get("format") == FORMAT:
        with io.BufferedReader(
            Blocks(read_blocks(config, descriptor)), buffer_size=65536
        ) as reader:
            yield reader
    else:
        from ingesta_delta_lake.association_storage import verify_artifact

        path = verify_artifact(config, descriptor)
        with gzip.open(path, "rb") as reader:
            yield reader


def items(config, descriptor, prefix, *, mapping=False, max_items=None):
    with stream(config, descriptor) as reader:
        parser = ijson.kvitems if mapping else ijson.items
        for n, item in enumerate(parser(reader, prefix, use_float=True), 1):
            if max_items is not None and n > max_items:
                raise IntegrityError("Association metadata exceeds its explicit row budget")
            yield item
