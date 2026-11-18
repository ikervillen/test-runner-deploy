"""Lossless shared blocks for association snapshots; bounded streaming memory.

Boundaries follow hashes of JSON object segments, so inserting records does not
shift all subsequent blocks. The bytes remain identical, including legacy JSON.
"""

import gzip
import hashlib
import json
import os
import re
import uuid
import zlib
from pathlib import Path

from .common import IntegrityError, canonical

FORMAT = "association-cas-v1"
MIN_BLOCK = 32 * 1024
MAX_BLOCK = 256 * 1024


def root_for(config):
    publication = getattr(config, "publication_root", None)
    return (
        publication / "artifacts" / "association-artifacts"
        if publication
        else config.control_root / "association-artifacts"
    )


def sync(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def publish(path, data):
    """Publish immutable, durable bytes; never overwrite an existing object."""
    if path.is_symlink():
        raise IntegrityError("Association object is a symbolic link")
    if path.exists():
        if path.read_bytes() != data:
            raise IntegrityError("Association object inmutable altered")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    sync(path.parent.parent)
    temporary = path.with_name("." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise IntegrityError("Concurrent association object differs") from None
        sync(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def blocks(parts):
    """Content-defined boundaries, independent of upstream read buffer sizes."""
    buffer = bytearray()
    segment_hash = 0
    for data in parts:
        start = 0
        while start < len(data):
            end = data.find(b"}", start)
            stop = end + 1 if end >= 0 else len(data)
            segment = data[start:stop]
            segment_hash = zlib.crc32(segment, segment_hash)
            while segment:
                take = min(MAX_BLOCK - len(buffer), len(segment))
                buffer.extend(segment[:take])
                segment = segment[take:]
                if len(buffer) == MAX_BLOCK:
                    yield bytes(buffer)
                    buffer.clear()
            if end >= 0:
                if len(buffer) >= MIN_BLOCK and segment_hash & 63 == 0:
                    yield bytes(buffer)
                    buffer.clear()
                segment_hash = 0
            start = stop
    if buffer:
        yield bytes(buffer)


def encoded(value):
    parts, size = [], 0
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    for text in encoder.iterencode(value):
        parts.append(text)
        size += len(text)
        if size >= 65536:
            yield "".join(parts).encode("utf-8")
            parts, size = [], 0
    if parts:
        yield "".join(parts).encode("utf-8")


def object_path(root, sha):
    if not isinstance(sha, str) or not re.fullmatch("[a-f0-9]{64}", sha):
        raise IntegrityError("Invalid association block identity")
    path = root / "objects" / sha[:2] / (sha + ".gz")
    if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
        raise IntegrityError("Association block escapes its root")
    return path


def write(root, parts, *, verified=None):
    if root.is_symlink():
        raise IntegrityError("Association root is a symbolic link")
    root.mkdir(parents=True, exist_ok=True)
    sync(root.parent)
    verified = {} if verified is None else verified
    chunks, total, payload = [], 0, hashlib.sha256()
    for block in blocks(parts):
        sha = hashlib.sha256(block).hexdigest()
        path = object_path(root, sha)
        if sha not in verified:
            if not path.exists():
                publish(path, gzip.compress(block, compresslevel=1, mtime=0))
            stored = read_block(root, sha)
            if stored != block:
                raise IntegrityError("Association block inmutable altered")
            verified[sha] = len(stored)
        chunks.append(sha)
        payload.update(block)
        total += len(block)
    manifest = {
        "format": FORMAT,
        "payload_hash": payload.hexdigest(),
        "bytes": total,
        "chunks": chunks,
    }
    data = gzip.compress(canonical(manifest).encode(), compresslevel=1, mtime=0)
    sha = hashlib.sha256(data).hexdigest()
    path = root / "manifests" / (sha + ".json.gz")
    publish(path, data)
    return {"format": FORMAT, "path": str(path), "sha256": sha, "payload_hash": payload.hexdigest()}


def resolve(config, descriptor):
    """Legacy descriptors survive conversion, including archived batch receipts."""
    root = root_for(config)
    if descriptor.get("format") == FORMAT:
        return descriptor
    sha = descriptor["sha256"]
    if not re.fullmatch("[a-f0-9]{64}", sha):
        raise IntegrityError("Invalid legacy association identity")
    redirect = root / "redirects" / (sha + ".json")
    if redirect.is_symlink() or not redirect.resolve().is_relative_to(root.resolve()):
        raise IntegrityError("Association redirect escapes its root")
    if not redirect.exists():
        return descriptor
    value = json.loads(redirect.read_text())
    if (
        value["original"] != descriptor
        or value["replacement"]["payload_hash"] != descriptor["payload_hash"]
    ):
        raise IntegrityError("Association redirect identity differs")
    return value["replacement"]


def manifest(config, descriptor):
    root = root_for(config)
    path = Path(descriptor["path"])
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise IntegrityError("Association manifest escapes its root")
    with path.open("rb") as stream:
        sha = hashlib.file_digest(stream, "sha256").hexdigest()
    if sha != descriptor["sha256"]:
        raise IntegrityError("Association manifest checksum differs")
    with gzip.open(path, "rb") as stream:
        raw = stream.read(64 * 1024**2 + 1)
    if len(raw) > 64 * 1024**2:
        raise IntegrityError("Association manifest exceeds its budget")
    document = json.loads(raw)
    if document["format"] != FORMAT or document["payload_hash"] != descriptor["payload_hash"]:
        raise IntegrityError("Association manifest identity differs")
    return document


def read_block(root, sha):
    with gzip.open(object_path(root, sha), "rb") as stream:
        block = stream.read(MAX_BLOCK + 1)
    if len(block) > MAX_BLOCK or hashlib.sha256(block).hexdigest() != sha:
        raise IntegrityError("Association block inmutable checksum differs")
    return block


def verify_shared(config, descriptor, verified):
    """Read back each immutable block once per conversion batch, not per snapshot.

    The conversion separately checks the complete source payload hash while it
    builds this immutable manifest. No historical data is deserialized here.
    """
    root = root_for(config)
    document = manifest(config, descriptor)
    size = 0
    for sha in document["chunks"]:
        if sha not in verified:
            verified[sha] = len(read_block(root, sha))
        size += verified[sha]
    if size != document["bytes"]:
        raise IntegrityError("Association manifest size differs")


def read_blocks(config, descriptor):
    root = root_for(config)
    document = manifest(config, descriptor)
    payload, size = hashlib.sha256(), 0
    for sha in document["chunks"]:
        block = read_block(root, sha)
        payload.update(block)
        size += len(block)
        yield block
    if size != document["bytes"] or payload.hexdigest() != descriptor["payload_hash"]:
        raise IntegrityError("Association payload checksum differs")
