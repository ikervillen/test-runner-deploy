"""Explicit operating policy and disjoint storage for independent pipelines."""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .common import ConfigurationError, digest

LANES = ("live", "history")
STORES = ("bronze", "silver", "gold", "control", "checkpoint", "exchange")


def _positive(value, name):
    if type(value) is not int or value <= 0:
        raise ConfigurationError(f"{name} must be a positive integer")
    return value


def _fields(value, expected, name):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ConfigurationError(f"Unexpected or missing fields in {name}")


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime

    def contains(self, stamp):
        return stamp is not None and self.start <= stamp < self.end


@dataclass(frozen=True)
class PipelinePolicy:
    timezone: str
    window_seconds: int
    retention_seconds: int
    latency_seconds: int
    poll_seconds: int
    history_batch_files: int
    history_turn_seconds: int
    stable_wait_seconds: float
    capture_attempts: int
    max_file_bytes: int
    archive_batch_rows: int

    def __post_init__(self):
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, TypeError) as exc:
            raise ConfigurationError("Unknown source timezone") from exc
        for name in (
            "window_seconds",
            "retention_seconds",
            "latency_seconds",
            "poll_seconds",
            "history_batch_files",
            "history_turn_seconds",
            "capture_attempts",
            "max_file_bytes",
            "archive_batch_rows",
        ):
            _positive(getattr(self, name), name)
        if self.retention_seconds < self.window_seconds:
            raise ConfigurationError("Live retention must cover at least one source window")
        if self.poll_seconds >= self.latency_seconds:
            raise ConfigurationError("Polling must leave time for capture and publication")
        if (
            isinstance(self.stable_wait_seconds, bool)
            or not isinstance(self.stable_wait_seconds, (int, float))
            or not 0 < self.stable_wait_seconds < self.latency_seconds
        ):
            raise ConfigurationError("Invalid stable capture wait")

    def source_window(self, latest: datetime) -> Window:
        """Latest producer slot, even when Fenix is behind the wall clock."""
        if latest.tzinfo is not None:
            raise ConfigurationError("Fenix source clocks must retain their local-naive policy")
        midnight = latest.replace(hour=0, minute=0, second=0, microsecond=0)
        elapsed = (latest - midnight).total_seconds()
        start = midnight + timedelta(seconds=(elapsed // self.window_seconds) * self.window_seconds)
        return Window(start, start + timedelta(seconds=self.window_seconds))

    def archive_before(self, now: datetime) -> datetime:
        if now.tzinfo is None:
            raise ConfigurationError("Publication retention requires an aware technical clock")
        return now - timedelta(seconds=self.retention_seconds)


@dataclass(frozen=True)
class LaneStorage:
    bronze: Path
    silver: Path
    gold: Path
    control: Path
    checkpoint: Path
    exchange: Path


@dataclass(frozen=True)
class IndependentPipelines:
    policy: PipelinePolicy
    live: LaneStorage
    history: LaneStorage

    def __post_init__(self):
        paths = [getattr(getattr(self, lane), name) for lane in LANES for name in STORES]
        for path in paths:
            if not path.is_absolute() or path != path.resolve() or path == Path(path.anchor):
                raise ConfigurationError("Pipeline stores must be resolved absolute directories")
            if str(path).startswith("\\\\"):
                raise ConfigurationError("Pipeline controls require local persistent storage")
            if path.exists() and not path.is_dir():
                raise ConfigurationError("Pipeline store is not a directory")
        for index, left in enumerate(paths):
            for right in paths[index + 1 :]:
                if left.is_relative_to(right) or right.is_relative_to(left):
                    raise ConfigurationError("Pipeline stores must not share or contain each other")
                if left.exists() and right.exists() and left.samefile(right):
                    raise ConfigurationError("Pipeline stores alias the same directory")

    @classmethod
    def load(cls, path: Path):
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
        _fields(value, ("schema_id", "policy", "live", "history"), "pipelines")
        if value["schema_id"] != "fenix-independent-pipelines-v1":
            raise ConfigurationError("Unsupported independent pipeline configuration")
        _fields(value["policy"], PipelinePolicy.__dataclass_fields__, "policy")
        storage = {}
        for lane in LANES:
            _fields(value[lane], STORES, lane)
            if any(not isinstance(p, str) or not p for p in value[lane].values()):
                raise ConfigurationError("Storage paths must be nonempty strings")
            storage[lane] = LaneStorage(**{k: Path(v) for k, v in value[lane].items()})
        return cls(policy=PipelinePolicy(**value["policy"]), **storage)

    def lane(self, name):
        if name not in LANES:
            raise ConfigurationError("An independent worker requires live or history")
        return getattr(self, name)

    def identity(self, lane):
        """Durable identity excludes releases, activation tokens and process IDs."""
        from dataclasses import asdict

        return digest([lane, {k: str(v) for k, v in asdict(self.lane(lane)).items()}])
