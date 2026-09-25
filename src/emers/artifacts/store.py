"""Discovery and loading of measurement artifacts in an EMERS workspace.

The store is deliberately read-only. Measurement collection owns artifact
creation; dashboards, reports, and future API layers consume them through this
module instead of depending on the workspace directory layout directly.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import csv
from dataclasses import dataclass
from io import BytesIO, StringIO
import json
from pathlib import Path
from threading import Lock, RLock
from typing import Iterable

import pandas as pd


_CACHE_COUNTER_NAMES = (
    "manifest_hits",
    "manifest_reads",
    "events_hits",
    "events_full_reads",
    "events_incremental_reads",
    "csv_hits",
    "csv_full_reads",
    "csv_incremental_reads",
)


@dataclass(frozen=True)
class MeasurementSource:
    """A configured source that has measurement artifacts on disk."""

    name: str
    path: Path


@dataclass(frozen=True)
class ExperimentArtifacts:
    """Artifacts belonging to one experiment under one measurement source."""

    source: str
    name: str
    path: Path


@dataclass(frozen=True)
class MeasurementFile:
    """One CSV chunk produced during an experiment."""

    source: str
    experiment: str
    path: Path

    @property
    def name(self) -> str:
        return self.path.name


@dataclass(frozen=True)
class RunArtifact:
    """Manifest, events, and measurements belonging to one EMERS run."""

    source: str
    experiment: str
    run_id: str
    path: Path
    experiment_path: Path

    @property
    def manifest_path(self) -> Path:
        return self.path / "run.json"

    @property
    def events_path(self) -> Path:
        return self.path / "events.jsonl"


@dataclass(frozen=True)
class _FileIdentity:
    device: int
    inode: int


@dataclass
class _JsonCacheEntry:
    signature: tuple[int, int, int, int]
    value: dict


@dataclass
class _JsonLinesCacheEntry:
    identity: _FileIdentity
    mtime_ns: int
    observed_size: int
    parsed_offset: int
    values: tuple[dict, ...]


@dataclass
class _CsvCacheEntry:
    identity: _FileIdentity
    mtime_ns: int
    observed_size: int
    parsed_offset: int
    columns: tuple[str, ...]
    frame: pd.DataFrame


def _identity(stat_result) -> _FileIdentity:
    return _FileIdentity(stat_result.st_dev, stat_result.st_ino)


def _signature(path: Path) -> tuple[int, int, int, int]:
    stat_result = path.stat()
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_size,
        stat_result.st_mtime_ns,
    )


def _complete_lines(data: bytes) -> tuple[bytes, int]:
    """Return bytes through the last complete line and their consumed length."""

    end = data.rfind(b"\n")
    if end < 0:
        return b"", 0
    consumed = end + 1
    return data[:consumed], consumed


def _complete_csv_records(
    data: bytes, columns: tuple[str, ...] = ()
) -> tuple[bytes, int]:
    """Keep a partial live row pending, while accepting a valid final row."""

    complete, consumed = _complete_lines(data)
    if not data or data.endswith(b"\n"):
        return complete, consumed
    try:
        rows = list(csv.reader(StringIO(data.decode("utf-8")), strict=True))
    except (csv.Error, UnicodeDecodeError):
        return complete, consumed
    if not rows:
        return complete, consumed
    expected_fields = len(columns) if columns else len(rows[0])
    if expected_fields and len(rows[-1]) == expected_fields:
        return data, len(data)
    return complete, consumed


class ArtifactStore:
    """Discover and load artifacts rooted at an EMERS workspace."""

    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace).expanduser().resolve()
        self.measurements_path = self.workspace / "measurements"
        self._cache_lock = RLock()
        self._path_locks: dict[Path, Lock] = {}
        self._manifest_cache: dict[Path, _JsonCacheEntry] = {}
        self._events_cache: dict[Path, _JsonLinesCacheEntry] = {}
        self._csv_cache: dict[Path, _CsvCacheEntry] = {}
        self._runs_by_path: dict[Path, RunArtifact] = {}
        self._cache_statistics = Counter()

    def _path_lock(self, path: Path) -> Lock:
        with self._cache_lock:
            return self._path_locks.setdefault(path, Lock())

    def cache_info(self) -> dict[str, int]:
        """Return cache counters and entry counts for diagnostics and tests."""

        with self._cache_lock:
            return {
                **{
                    name: self._cache_statistics[name]
                    for name in _CACHE_COUNTER_NAMES
                },
                "manifest_entries": len(self._manifest_cache),
                "events_entries": len(self._events_cache),
                "csv_entries": len(self._csv_cache),
                "run_entries": len(self._runs_by_path),
            }

    def clear_cache(self) -> None:
        """Drop all cached artifact contents and reset diagnostic counters."""

        with self._cache_lock:
            self._manifest_cache.clear()
            self._events_cache.clear()
            self._csv_cache.clear()
            self._runs_by_path.clear()
            self._cache_statistics.clear()

    def sources(self) -> tuple[MeasurementSource, ...]:
        if not self.measurements_path.is_dir():
            return ()
        return tuple(
            MeasurementSource(path.name, path)
            for path in sorted(self.measurements_path.iterdir(), key=lambda item: item.name)
            if path.is_dir() and not path.name.startswith(".")
        )

    def experiments(
        self, source: MeasurementSource | str | Path
    ) -> tuple[ExperimentArtifacts, ...]:
        source_path = source.path if isinstance(source, MeasurementSource) else Path(source)
        if not source_path.is_absolute():
            source_path = self.measurements_path / source_path
        if not source_path.is_dir():
            return ()
        return tuple(
            ExperimentArtifacts(source_path.name, path.name, path)
            for path in sorted(source_path.iterdir(), key=lambda item: item.name)
            if path.is_dir() and not path.name.startswith(".")
        )

    def files(
        self, experiment: ExperimentArtifacts | str | Path
    ) -> tuple[MeasurementFile, ...]:
        experiment_path = (
            experiment.path if isinstance(experiment, ExperimentArtifacts) else Path(experiment)
        )
        if not experiment_path.is_dir():
            return ()
        return tuple(
            MeasurementFile(
                source=experiment_path.parent.name,
                experiment=experiment_path.name,
                path=path,
            )
            for path in sorted(experiment_path.glob("*.csv"), key=lambda item: item.name)
            if path.is_file()
        )

    def runs(self) -> tuple[RunArtifact, ...]:
        """Discover runs, newest manifest first."""

        runs = []
        for source in self.sources():
            for experiment in self.experiments(source):
                runs_path = experiment.path / ".emers" / "runs"
                if not runs_path.is_dir():
                    continue
                for path in runs_path.iterdir():
                    if path.is_dir() and (path / "run.json").is_file():
                        runs.append(
                            RunArtifact(
                                source=source.name,
                                experiment=experiment.name,
                                run_id=path.name,
                                path=path,
                                experiment_path=experiment.path,
                            )
                        )
        discovered = tuple(
            sorted(
                runs,
                key=lambda run: run.manifest_path.stat().st_mtime,
                reverse=True,
            )
        )
        with self._cache_lock:
            self._runs_by_path = {run.path: run for run in discovered}
        return discovered

    def run(self, value: RunArtifact | str | Path) -> RunArtifact:
        """Resolve a discovered run from its directory or manifest path."""

        if isinstance(value, RunArtifact):
            return value
        candidate = Path(value).expanduser()
        if candidate.name == "run.json":
            candidate = candidate.parent
        candidate = candidate.resolve()
        with self._cache_lock:
            cached = self._runs_by_path.get(candidate)
        if cached is not None:
            return cached
        for run in self.runs():
            if run.path == candidate:
                return run
        raise ValueError(f"Run artifact not found in workspace: {value}")

    def resolve_run(self, value: RunArtifact | str | Path | None = None) -> RunArtifact:
        """Resolve the latest run, a path, or an unambiguous run-ID prefix."""

        runs = self.runs()
        if value is None:
            if not runs:
                raise ValueError(f"No run manifests found in {self.workspace}")
            return runs[0]
        if isinstance(value, RunArtifact):
            return value

        requested = str(value)
        candidate = Path(requested).expanduser()
        workspace_candidate = self.workspace / candidate
        if candidate.exists():
            return self.run(candidate)
        if not candidate.is_absolute() and workspace_candidate.exists():
            return self.run(workspace_candidate)

        matches = [run for run in runs if run.run_id.startswith(requested)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f"Run ID prefix is ambiguous: {requested!r}")
        raise ValueError(f"Run artifact not found in workspace: {value}")

    def read_manifest(self, run: RunArtifact | str | Path) -> dict:
        artifact = self.run(run)
        path = artifact.manifest_path.resolve()
        lock = self._path_lock(path)
        with lock:
            signature = _signature(path)
            with self._cache_lock:
                entry = self._manifest_cache.get(path)
                if entry is not None and entry.signature == signature:
                    self._cache_statistics["manifest_hits"] += 1
                    return deepcopy(entry.value)

            value = json.loads(path.read_text(encoding="utf-8"))
            with self._cache_lock:
                self._manifest_cache[path] = _JsonCacheEntry(signature, value)
                self._cache_statistics["manifest_reads"] += 1
            return deepcopy(value)

    def read_events(self, run: RunArtifact | str | Path) -> tuple[dict, ...]:
        artifact = self.run(run)
        path = artifact.events_path.resolve()
        if not path.is_file():
            return ()
        lock = self._path_lock(path)
        with lock:
            stat_result = path.stat()
            identity = _identity(stat_result)
            with self._cache_lock:
                entry = self._events_cache.get(path)

            if (
                entry is not None
                and entry.identity == identity
                and entry.observed_size == stat_result.st_size
                and entry.mtime_ns == stat_result.st_mtime_ns
            ):
                with self._cache_lock:
                    self._cache_statistics["events_hits"] += 1
                return deepcopy(entry.values)

            can_append = (
                entry is not None
                and entry.identity == identity
                and stat_result.st_size > entry.observed_size
            )
            offset = entry.parsed_offset if can_append else 0
            with path.open("rb") as stream:
                stream.seek(offset)
                raw = stream.read()
            complete, consumed = _complete_lines(raw)
            parsed = tuple(
                json.loads(line)
                for line in complete.decode("utf-8").splitlines()
                if line.strip()
            )
            values = entry.values + parsed if can_append else parsed
            new_entry = _JsonLinesCacheEntry(
                identity=identity,
                mtime_ns=stat_result.st_mtime_ns,
                observed_size=offset + len(raw),
                parsed_offset=offset + consumed,
                values=values,
            )
            with self._cache_lock:
                self._events_cache[path] = new_entry
                counter = "events_incremental_reads" if can_append else "events_full_reads"
                self._cache_statistics[counter] += 1
            return deepcopy(values)

    @staticmethod
    def _parse_csv(data: bytes, columns: tuple[str, ...] = ()) -> pd.DataFrame:
        if not data:
            return pd.DataFrame(columns=list(columns))
        try:
            if columns:
                return pd.read_csv(BytesIO(data), header=None, names=list(columns))
            return pd.read_csv(BytesIO(data))
        except pd.errors.EmptyDataError:
            return pd.DataFrame(columns=list(columns))

    def _read_file_cached(self, file: MeasurementFile | str | Path) -> pd.DataFrame:
        path = file.path if isinstance(file, MeasurementFile) else Path(file)
        path = path.expanduser().resolve()
        lock = self._path_lock(path)
        with lock:
            stat_result = path.stat()
            identity = _identity(stat_result)
            with self._cache_lock:
                entry = self._csv_cache.get(path)

            if (
                entry is not None
                and entry.identity == identity
                and entry.observed_size == stat_result.st_size
                and entry.mtime_ns == stat_result.st_mtime_ns
            ):
                with self._cache_lock:
                    self._cache_statistics["csv_hits"] += 1
                return entry.frame

            can_append = (
                entry is not None
                and entry.identity == identity
                and stat_result.st_size > entry.observed_size
                and bool(entry.columns)
            )
            offset = entry.parsed_offset if can_append else 0
            with path.open("rb") as stream:
                stream.seek(offset)
                raw = stream.read()
            complete, consumed = _complete_csv_records(
                raw, entry.columns if can_append else ()
            )

            if can_append:
                appended = self._parse_csv(complete, entry.columns)
                frame = (
                    pd.concat((entry.frame, appended), ignore_index=True)
                    if not appended.empty
                    else entry.frame
                )
                columns = entry.columns
            else:
                frame = self._parse_csv(complete)
                columns = tuple(frame.columns)

            new_entry = _CsvCacheEntry(
                identity=identity,
                mtime_ns=stat_result.st_mtime_ns,
                observed_size=offset + len(raw),
                parsed_offset=offset + consumed,
                columns=columns,
                frame=frame,
            )
            with self._cache_lock:
                self._csv_cache[path] = new_entry
                counter = "csv_incremental_reads" if can_append else "csv_full_reads"
                self._cache_statistics[counter] += 1
            return frame

    def read_file(self, file: MeasurementFile | str | Path) -> pd.DataFrame:
        """Read one CSV chunk with automatic append-aware caching."""

        return self._read_file_cached(file).copy(deep=True)

    def read_files(
        self, files: Iterable[MeasurementFile | str | Path]
    ) -> pd.DataFrame:
        """Read and concatenate CSV chunks in deterministic filename order."""

        paths = sorted(
            (
                file.path if isinstance(file, MeasurementFile) else Path(file)
                for file in files
            ),
            key=lambda path: path.name,
        )
        paths = [path for path in paths if path.is_file() and path.suffix == ".csv"]
        if not paths:
            return pd.DataFrame()
        frames = [self._read_file_cached(path) for path in paths]
        populated = [frame for frame in frames if not frame.empty]
        return pd.concat(populated, ignore_index=True) if populated else pd.DataFrame()

    def read_experiment(
        self, experiment: ExperimentArtifacts | str | Path
    ) -> pd.DataFrame:
        return self.read_files(self.files(experiment))

    def read_run(self, run: RunArtifact | str | Path) -> pd.DataFrame:
        """Load only CSV rows associated with one run ID."""

        artifact = self.run(run)
        readings = self.read_experiment(artifact.experiment_path)
        if readings.empty or "run_id" not in readings.columns:
            return pd.DataFrame(columns=readings.columns)
        return readings[readings["run_id"] == artifact.run_id].reset_index(drop=True)

    def validate_run(
        self,
        run: RunArtifact | str | Path,
        *,
        manifest=None,
        readings=None,
        strict_benchmark=False,
    ) -> dict:
        """Validate a run without reopening CSV files already held by the store."""

        from emers.validation import validate_manifest

        artifact = self.run(run)
        manifest = manifest if manifest is not None else self.read_manifest(artifact)
        readings = readings if readings is not None else self.read_run(artifact)
        rows = tuple(
            (
                f"{artifact.experiment_path}:cached-row-{index + 2}",
                record,
            )
            for index, record in enumerate(readings.to_dict("records"))
        )
        return validate_manifest(
            artifact.manifest_path,
            strict_benchmark=strict_benchmark,
            manifest=manifest,
            rows=rows,
        )

    def read_all(self) -> dict[Path, pd.DataFrame]:
        """Load every non-empty experiment in the workspace."""

        result = {}
        for source in self.sources():
            for experiment in self.experiments(source):
                readings = self.read_experiment(experiment)
                if not readings.empty:
                    result[experiment.path] = readings
        return result


class RunStore(ArtifactStore):
    """Canonical run-oriented artifact store.

    ``ArtifactStore`` remains available for compatibility with the original
    discovery API; both classes provide the same change-aware caches.
    """
