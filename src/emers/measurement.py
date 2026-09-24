"""Resilient measurement collection and run artifact recording."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import importlib.metadata
import json
import statistics
import threading
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic, time
from typing import Optional


CSV_FIELDS = (
    "timestamp",
    "timestamp_unit",
    "timestamp_monotonic",
    "timestamp_monotonic_unit",
    "request_started_at",
    "request_ended_at",
    "request_wall_clock_unit",
    "request_started_monotonic",
    "request_ended_monotonic",
    "request_monotonic_unit",
    "acquisition_latency_seconds",
    "requested_polling_interval_seconds",
    "effective_polling_interval_seconds",
    "achieved_polling_interval_seconds",
    "current_draw",
    "current_draw_unit",
    "total_draw",
    "total_draw_unit",
    "source",
    "provider",
    "measurement_method",
    "measurement_scope",
    "segment_id",
    "segment_index",
    "run_id",
    "misc",
)


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _deep_update(target, changes):
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


@dataclass
class MeasurementLogResult:
    """One normalized energy measurement."""

    timestamp: float
    current_draw: float
    total_draw: float
    misc: Optional[dict] = None
    source: str = "unknown"
    measurement_method: str = "unknown"
    measurement_scope: str = "unknown"
    timestamp_monotonic: Optional[float] = None
    request_started_at: Optional[float] = None
    request_ended_at: Optional[float] = None
    request_started_monotonic: Optional[float] = None
    request_ended_monotonic: Optional[float] = None
    acquisition_latency_seconds: Optional[float] = None
    requested_polling_interval_seconds: Optional[float] = None
    effective_polling_interval_seconds: Optional[float] = None
    achieved_polling_interval_seconds: Optional[float] = None


SENSITIVE_CONFIG_PARTS = ("password", "secret", "token", "credential", "api_key", "user")


def _sanitized_config(value):
    if isinstance(value, dict):
        return {
            key: _sanitized_config(item)
            for key, item in value.items()
            if not any(part in key.lower() for part in SENSITIVE_CONFIG_PARTS)
            and key != "fallbacks"
        }
    if isinstance(value, (list, tuple)):
        return [_sanitized_config(item) for item in value]
    return value


def _sanitized_full_config(value):
    if isinstance(value, dict):
        return {
            key: _sanitized_full_config(item)
            for key, item in value.items()
            if not any(part in key.lower() for part in SENSITIVE_CONFIG_PARTS)
        }
    if isinstance(value, (list, tuple)):
        return [_sanitized_full_config(item) for item in value]
    return value


def _provider_versions(provider_name):
    distributions = {
        "codecarbon": ("codecarbon",),
        "zeus": ("zeus",),
        "tapo": ("tapo",),
        "shelly": ("requests",),
    }.get(provider_name, (provider_name,))
    versions = {}
    for distribution in distributions:
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            continue
    return versions


def _emers_version():
    try:
        return importlib.metadata.version("emers")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.0+source"


def _config_hash(settings):
    payload = json.dumps(
        _sanitized_full_config(settings),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class MeasurementUnavailableError(RuntimeError):
    """Raised when the configured measurement policy cannot continue."""


class MeasurementManager:
    """Collect and persist measurements with explicit outage handling.

    ``failure_policy`` controls provider failures:

    - ``fallback`` (default): try configured fallback providers in order after
      the active provider is unavailable. Each provider receives a separate
      measurement segment; incompatible cumulative totals are never stitched.
    - ``continue``: keep retrying the primary provider with exponential
      backoff. The experiment continues and the run is marked degraded.
    - ``fail``: fail startup immediately or stop collection after
      ``max_failures`` consecutive runtime failures. Background failures are
      raised when the context exits.
    """

    def __init__(
        self,
        source_name=None,
        experiment_name=None,
        polling_rate=0.5,
        log_interval=300,
        workspace=None,
        config=None,
        *,
        device_name=None,
        failure_policy="fallback",
        fallback_source=None,
        fallback_device=None,
        fallbacks=None,
        max_failures=3,
        request_timeout=10.0,
        retry_backoff=1.0,
        max_retry_backoff=30.0,
        startup_timeout=30.0,
        run_id=None,
        metadata=None,
        tracker_factory=None,
    ):
        if failure_policy not in {"fallback", "continue", "fail"}:
            raise ValueError("failure_policy must be 'fallback', 'continue', or 'fail'")
        if polling_rate <= 0 or log_interval <= 0:
            raise ValueError("polling_rate and log_interval must be positive")
        if max_failures < 1:
            raise ValueError("max_failures must be at least 1")

        self.stop_event = threading.Event()
        self.ready_event = threading.Event()
        self.loop_thread = None
        self.background_error = None
        if source_name is not None and device_name is not None and source_name != device_name:
            raise ValueError("source_name and legacy device_name refer to different sources")
        self.source_name = source_name if source_name is not None else device_name
        if self.source_name is None:
            raise TypeError("source_name is required")
        # Compatibility attributes for callers using the pre-provider vocabulary.
        self.device_name = self.source_name
        self.experiment_name = experiment_name or "continuous"
        self.polling_rate = float(polling_rate)
        self.log_interval = int(log_interval)
        self.failure_policy = failure_policy
        if fallback_source is not None and fallback_device is not None:
            if fallback_source != fallback_device:
                raise ValueError(
                    "fallback_source and legacy fallback_device refer to different sources"
                )
        requested_fallback = (
            fallback_source if fallback_source is not None else fallback_device
        )
        self._requested_fallbacks = fallbacks
        self._requested_fallback = requested_fallback
        self.max_failures = int(max_failures)
        self.request_timeout = float(request_timeout)
        self.retry_backoff = float(retry_backoff)
        self.max_retry_backoff = float(max_retry_backoff)
        self.startup_timeout = float(startup_timeout)
        self.run_id = str(run_id or uuid.uuid4())
        self.workspace = Path(workspace or ".").expanduser().resolve()
        self._tracker_factory = tracker_factory
        self._artifact_lock = threading.RLock()
        self._artifacts_ready = False
        self._had_errors = False
        self._fallback_active = False
        self._segments = []
        self._current_segment = None
        self._segment_intervals = {}
        self._last_sample_monotonic = None
        self._measurement_started_monotonic = None
        self._measurement_started_wall = None
        self._gap = None
        self._gaps = []
        self._sample_count = 0
        self._log_started_at = None
        self._log_file = None

        self.config_path = Path(config or "settings.json").expanduser()
        if not self.config_path.is_absolute():
            self.config_path = self.workspace / self.config_path
        try:
            with self.config_path.open("r", encoding="utf-8") as file:
                self.sources = json.load(file)
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"Configuration not found at {self.config_path}. Run 'emers init' first."
            ) from exc

        if self.source_name not in self.sources:
            raise ValueError(f"Source {self.source_name} not found in {self.config_path}")
        self.source_settings = self.sources[self.source_name]
        self.devices = self.sources
        self.device = self.source_settings
        if self._requested_fallbacks is not None and self._requested_fallback is not None:
            raise ValueError("Use either fallbacks or fallback_source, not both")
        configured_fallbacks = self.source_settings.get("fallbacks")
        if self._requested_fallbacks is not None:
            resolved_fallbacks = self._requested_fallbacks
        elif self._requested_fallback is not None:
            resolved_fallbacks = [self._requested_fallback]
        elif configured_fallbacks is not None:
            resolved_fallbacks = configured_fallbacks
        else:
            primary_provider = (
                self.source_settings.get("provider")
                or self.source_settings.get("device_type")
            )
            resolved_fallbacks = [] if primary_provider == "codecarbon" else ["codecarbon"]
        if not isinstance(resolved_fallbacks, (list, tuple)):
            raise ValueError("fallbacks must be an ordered list")
        self.fallbacks = list(resolved_fallbacks)
        self.fallback_source = self.fallbacks[0] if self.fallbacks else None
        self.fallback_device = self.fallback_source

        self.log_base = (
            self.workspace / "measurements" / self.source_name / self.experiment_name
        )
        self.artifact_dir = self.log_base / ".emers" / "runs" / self.run_id
        self.manifest_path = self.artifact_dir / "run.json"
        self.events_path = self.artifact_dir / "events.jsonl"
        self.manifest = {
            "schema_version": 2,
            "run_id": self.run_id,
            "experiment": self.experiment_name,
            "source": self.source_name,
            "device": self.source_name,
            "created_at": _utc_now(),
            "status": "initialized",
            "software": {"emers_version": _emers_version()},
            "metadata": metadata or {},
            "parameters": {},
            "metrics": {},
            "measurement": {
                "schema": {
                    "timestamp": {"unit": "unix_seconds", "clock": "wall"},
                    "timestamp_monotonic": {"unit": "seconds", "clock": "monotonic"},
                    "current_draw": {"unit": "W", "quantity": "power"},
                    "total_draw": {
                        "unit": "kWh",
                        "quantity": "provider_local_cumulative_energy",
                    },
                    "acquisition_latency": {"unit": "seconds"},
                    "polling_intervals": {"unit": "seconds"},
                },
                "primary_provider": (
                    self.source_settings.get("provider")
                    or self.source_settings.get("device_type", "unknown")
                ),
                "active_provider": None,
                "method": None,
                "scope": None,
                "failure_policy": self.failure_policy,
                "status": "initializing",
                "error_count": 0,
                "fallback_used": False,
                "fallback_chain": [
                    self._fallback_description(spec) for spec in self.fallbacks
                ],
                "segments": [],
                "gaps": [],
                "coverage": None,
                "calibration": _sanitized_config(
                    self.source_settings.get("calibration", {})
                ),
                "source_configuration_sha256": _config_hash(self.source_settings),
            },
        }

    def _fallback_description(self, spec):
        if isinstance(spec, str):
            return {"source": spec} if spec in self.sources else {"provider": spec}
        if isinstance(spec, dict):
            if "source" in spec:
                return {"source": spec["source"]}
            return {
                "provider": spec.get("provider") or spec.get("device_type", "unknown")
            }
        return {"invalid": repr(spec)}

    def _ensure_artifacts(self):
        with self._artifact_lock:
            if self._artifacts_ready:
                return
            self.log_base.mkdir(exist_ok=True, parents=True)
            self.artifact_dir.mkdir(exist_ok=True, parents=True)
            self._artifacts_ready = True
            self._write_manifest()

    def _write_manifest(self):
        self.artifact_dir.mkdir(exist_ok=True, parents=True)
        temporary = self.manifest_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self.manifest, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.manifest_path)

    def update_manifest(self, changes):
        self._ensure_artifacts()
        with self._artifact_lock:
            _deep_update(self.manifest, changes)
            self._write_manifest()

    def record_event(self, event, **details):
        self._ensure_artifacts()
        payload = {
            "run_id": self.run_id,
            "timestamp": time(),
            "timestamp_iso": _utc_now(),
            "event": event,
            **details,
        }
        with self._artifact_lock:
            with self.events_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(payload, sort_keys=True, default=str) + "\n")

    def _provider_settings(self, fallback=False, fallback_index=0):
        if not fallback:
            return self.source_settings
        if fallback_index >= len(self.fallbacks):
            raise IndexError("Fallback provider index is out of range")
        configured = self.fallbacks[fallback_index]
        if isinstance(configured, dict):
            if "source" in configured:
                reference = configured["source"]
                if reference not in self.sources:
                    raise ValueError(
                        f"Fallback source {reference!r} not found in {self.config_path}"
                    )
                overrides = {key: value for key, value in configured.items() if key != "source"}
                return {**self.sources[reference], **overrides}
            return configured
        if isinstance(configured, str) and configured in self.sources:
            return self.sources[configured]
        if isinstance(configured, str):
            return {
                "provider": configured,
                "project_name": "emers",
                "experiment_name": self.experiment_name,
            }
        raise ValueError(f"Invalid fallback provider specification: {configured!r}")

    def _build_provider(self, fallback=False, fallback_index=0, settings=None):
        # Import lazily to avoid a module cycle: providers normalize results
        # using MeasurementLogResult from this module.
        from emers.providers import build_provider

        return build_provider(
            settings
            if settings is not None
            else self._provider_settings(
                fallback=fallback, fallback_index=fallback_index
            ),
            timeout=self.request_timeout,
            polling_rate=self.polling_rate,
            tracker_factory=self._tracker_factory,
        )

    @staticmethod
    def _capabilities(provider):
        capabilities = getattr(provider, "capabilities", None)
        if capabilities is not None and hasattr(capabilities, "to_dict"):
            return capabilities.to_dict()
        if isinstance(capabilities, dict):
            return capabilities
        return {
            "provider": provider.source,
            "method": provider.method,
            "scope": provider.scope,
            "components": [],
            "supports_periodic_readings": True,
        }

    def _finish_segment(self, status, reason=None):
        if self._current_segment is None or self._current_segment.get("ended_at"):
            return
        self._current_segment["ended_at"] = _utc_now()
        self._current_segment["status"] = status
        intervals = self._segment_intervals.get(self._current_segment["id"], [])
        if intervals:
            self._current_segment["achieved_polling_interval_seconds"] = {
                "count": len(intervals),
                "minimum": min(intervals),
                "maximum": max(intervals),
                "mean": statistics.fmean(intervals),
                "median": statistics.median(intervals),
            }
        if reason:
            self._current_segment["reason"] = reason
        self.update_manifest({"measurement": {"segments": list(self._segments)}})

    def _set_active_provider(self, provider, fallback=False, reason=None):
        if self._current_segment is not None:
            self._finish_segment("switched", reason=reason)
        segment = {
            "id": f"segment-{len(self._segments) + 1:03d}",
            "index": len(self._segments),
            "provider": provider.source,
            "method": provider.method,
            "scope": provider.scope,
            "capabilities": self._capabilities(provider),
            "versions": _provider_versions(provider.source),
            "configuration": _sanitized_config(
                getattr(provider, "settings", {})
            ),
            "calibration": _sanitized_config(
                getattr(provider, "settings", {}).get("calibration", {})
            ),
            "requested_polling_interval_seconds": self.polling_rate,
            "effective_polling_interval_seconds": provider.polling_interval,
            "sample_count": 0,
            "started_at": _utc_now(),
            "ended_at": None,
            "status": "active",
            "fallback": fallback,
        }
        self._segments.append(segment)
        self._current_segment = segment
        self._segment_intervals[segment["id"]] = []
        self._last_sample_monotonic = None
        self.update_manifest({
            "measurement": {
                "active_provider": provider.source,
                "method": provider.method,
                "scope": provider.scope,
                "status": "estimated" if fallback else "healthy",
                "fallback_used": fallback,
                "active_segment": segment["id"],
                "segments": list(self._segments),
            }
        })

    def _ensure_log_file(self, timestamp):
        if (
            self._log_file is None
            or self._log_started_at is None
            or timestamp >= self._log_started_at + self.log_interval
        ):
            self._log_started_at = timestamp
            self._log_file = self.log_base / f"{timestamp}.csv"
            with self._log_file.open("w", newline="", encoding="utf-8") as file:
                csv.DictWriter(file, fieldnames=CSV_FIELDS).writeheader()

    def _write_sample(self, result):
        self._ensure_artifacts()
        self._ensure_log_file(result.timestamp)
        row = asdict(result)
        row["timestamp_unit"] = "unix_seconds"
        row["timestamp_monotonic_unit"] = "seconds"
        row["request_wall_clock_unit"] = "unix_seconds"
        row["request_monotonic_unit"] = "seconds"
        row["current_draw_unit"] = "W"
        row["total_draw_unit"] = "kWh"
        row["source"] = self.source_name
        row["provider"] = (
            self._current_segment["provider"]
            if self._current_segment
            else result.source
        )
        row["segment_id"] = (
            self._current_segment["id"] if self._current_segment else "unsegmented"
        )
        row["segment_index"] = (
            self._current_segment["index"] if self._current_segment else -1
        )
        row["run_id"] = self.run_id
        row["misc"] = json.dumps(result.misc or {}, sort_keys=True, default=str)
        with self._log_file.open("a", newline="", encoding="utf-8") as file:
            csv.DictWriter(file, fieldnames=CSV_FIELDS).writerow(row)
        self._sample_count += 1
        if self._current_segment is not None:
            self._current_segment["sample_count"] += 1
            self._current_segment.setdefault("first_sample_at", result.timestamp)
            self._current_segment["last_sample_at"] = result.timestamp
            if result.achieved_polling_interval_seconds is not None:
                self._segment_intervals[self._current_segment["id"]].append(
                    result.achieved_polling_interval_seconds
                )
        if result.timestamp_monotonic is not None:
            self._last_sample_monotonic = result.timestamp_monotonic

    async def _acquire(self, provider, operation):
        request_started_at = time()
        request_started_monotonic = monotonic()
        try:
            result = await operation()
        except BaseException as exc:
            request_ended_at = time()
            request_ended_monotonic = monotonic()
            exc.emers_request_timing = {
                "request_started_at": request_started_at,
                "request_ended_at": request_ended_at,
                "request_started_monotonic": request_started_monotonic,
                "request_ended_monotonic": request_ended_monotonic,
                "acquisition_latency_seconds": (
                    request_ended_monotonic - request_started_monotonic
                ),
            }
            raise
        request_ended_at = time()
        request_ended_monotonic = monotonic()
        if result is None:
            return None
        achieved = (
            None
            if self._last_sample_monotonic is None
            else request_ended_monotonic - self._last_sample_monotonic
        )
        self._last_sample_monotonic = request_ended_monotonic
        return replace(
            result,
            timestamp_monotonic=request_ended_monotonic,
            request_started_at=request_started_at,
            request_ended_at=request_ended_at,
            request_started_monotonic=request_started_monotonic,
            request_ended_monotonic=request_ended_monotonic,
            acquisition_latency_seconds=(
                request_ended_monotonic - request_started_monotonic
            ),
            requested_polling_interval_seconds=self.polling_rate,
            effective_polling_interval_seconds=provider.polling_interval,
            achieved_polling_interval_seconds=achieved,
        )

    def _open_gap(self, provider, reason, timing=None):
        if self._gap is not None:
            return
        timing = timing or {}
        self._gap = {
            "provider": provider,
            "reason": reason,
            "started_at": timing.get("request_started_at", time()),
            "started_at_iso": datetime.fromtimestamp(
                timing.get("request_started_at", time()), timezone.utc
            ).isoformat(),
            "_started_monotonic": timing.get(
                "request_started_monotonic", monotonic()
            ),
        }
        self.record_event(
            "measurement_gap_started",
            provider=provider,
            reason=reason,
        )

    def _close_gap(self, resolution):
        if self._gap is None:
            return
        ended_monotonic = monotonic()
        gap = dict(self._gap)
        gap["ended_at"] = time()
        gap["ended_at_iso"] = _utc_now()
        gap["duration_seconds"] = ended_monotonic - gap.pop("_started_monotonic")
        gap["resolution"] = resolution
        self._gaps.append(gap)
        self._gap = None
        self.record_event(
            "measurement_gap_ended",
            provider=gap["provider"],
            duration_seconds=gap["duration_seconds"],
            resolution=resolution,
        )

    def _coverage(self):
        ended = monotonic()
        duration = max(0.0, ended - (self._measurement_started_monotonic or ended))
        gap_duration = sum(gap["duration_seconds"] for gap in self._gaps)
        covered = max(0.0, duration - gap_duration)
        return {
            "duration_seconds": duration,
            "covered_seconds": covered,
            "gap_seconds": gap_duration,
            "coverage_ratio": covered / duration if duration else None,
            "gap_count": len(self._gaps),
            "segment_count": len(self._segments),
            "sample_count": self._sample_count,
        }

    async def _sleep_interruptibly(self, seconds):
        deadline = asyncio.get_running_loop().time() + max(0.0, seconds)
        while not self.stop_event.is_set():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, 0.25))

    async def _activate_next_fallback(self, provider, started, reason, start_index):
        """Try remaining fallbacks in order and return the first that starts."""
        from_provider = getattr(provider, "source", "unavailable")
        for index in range(start_index, len(self.fallbacks)):
            fallback = None
            previous_sample_monotonic = self._last_sample_monotonic
            try:
                settings = self._provider_settings(
                    fallback=True, fallback_index=index
                )
                fallback = self._build_provider(settings=settings)
                self._last_sample_monotonic = None
                initial = await self._acquire(fallback, fallback.start)
            except Exception as exc:
                self._last_sample_monotonic = previous_sample_monotonic
                self._had_errors = True
                timing = getattr(exc, "emers_request_timing", {})
                self.record_event(
                    "fallback_unavailable",
                    fallback_index=index,
                    provider=getattr(
                        fallback,
                        "source",
                        self._fallback_description(self.fallbacks[index]),
                    ),
                    error_type=type(exc).__name__,
                    error=str(exc),
                    **timing,
                )
                print(f"EMERS fallback {index + 1} unavailable: {exc}")
                continue

            if started and provider is not None:
                try:
                    self._last_sample_monotonic = previous_sample_monotonic
                    final_sample = await self._acquire(provider, provider.stop)
                    if final_sample is not None:
                        self._write_sample(final_sample)
                except Exception as exc:
                    self.record_event(
                        "provider_stop_error",
                        provider=from_provider,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )

            self._fallback_active = True
            self._set_active_provider(fallback, fallback=True, reason=reason)
            self._close_gap(f"switched_to_{fallback.source}")
            self.record_event(
                "provider_switched",
                fallback_index=index,
                from_provider=from_provider,
                to_provider=fallback.source,
                reason=reason,
                method=fallback.method,
                scope=fallback.scope,
                segment_id=self._current_segment["id"],
            )
            print(
                f"EMERS switched from {from_provider} to {fallback.source}; subsequent "
                f"values are {fallback.method}/{fallback.scope} in a new segment."
            )
            if initial is not None:
                self._write_sample(initial)
            return fallback, True, index + 1

        print(
            f"EMERS has no available fallback after {from_provider}; "
            "continuing with explicit measurement gaps."
        )
        return None, started, len(self.fallbacks)

    async def log_data(self):
        """Collect measurements until ``stop_event`` is set."""
        self._ensure_artifacts()
        self._measurement_started_wall = time()
        self._measurement_started_monotonic = monotonic()
        self.update_manifest({"status": "running", "started_at": _utc_now()})
        self.record_event(
            "measurement_started",
            source=self.source_name,
            provider=(
                self.source_settings.get("provider")
                or self.source_settings.get("device_type")
            ),
            failure_policy=self.failure_policy,
        )

        provider = None
        started = False
        consecutive_failures = 0
        next_fallback_index = 0
        recovered_from_error = False

        try:
            try:
                provider = self._build_provider()
            except Exception as exc:
                self._had_errors = True
                self._open_gap("unavailable", str(exc))
                self.update_manifest({
                    "measurement": {
                        "status": "degraded",
                        "error_count": self.manifest["measurement"]["error_count"] + 1,
                        "last_error": str(exc),
                        "last_error_at": _utc_now(),
                    }
                })
                self.record_event(
                    "measurement_error",
                    provider="unavailable",
                    phase="provider_construction",
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                if self.failure_policy == "fallback":
                    provider, started, next_fallback_index = (
                        await self._activate_next_fallback(
                            None, False, reason=str(exc), start_index=0
                        )
                    )
                if provider is None:
                    raise MeasurementUnavailableError(
                        f"Primary provider could not be loaded: {exc}"
                    ) from exc
                self.ready_event.set()
                await self._sleep_interruptibly(provider.polling_interval)

            while not self.stop_event.is_set():
                try:
                    operation = provider.read if started else provider.start
                    result = await self._acquire(provider, operation)
                    started = True
                    if self._current_segment is None:
                        self._set_active_provider(
                            provider, fallback=self._fallback_active
                        )
                    if consecutive_failures:
                        self._close_gap("provider_recovered")
                        self.record_event(
                            "provider_recovered",
                            provider=provider.source,
                            consecutive_failures=consecutive_failures,
                        )
                        print(f"EMERS measurement provider {provider.source} recovered.")
                        recovered_from_error = True
                    consecutive_failures = 0
                    if result is not None:
                        self._write_sample(result)
                    self.ready_event.set()
                    await self._sleep_interruptibly(provider.polling_interval)
                except Exception as exc:
                    self._had_errors = True
                    consecutive_failures += 1
                    timing = getattr(exc, "emers_request_timing", {})
                    self._open_gap(provider.source, str(exc), timing=timing)
                    self.update_manifest({
                        "measurement": {
                            "status": "degraded",
                            "error_count": self.manifest["measurement"]["error_count"] + 1,
                            "last_error": str(exc),
                            "last_error_at": _utc_now(),
                        }
                    })
                    self.record_event(
                        "measurement_error",
                        provider=provider.source,
                        error_type=type(exc).__name__,
                        error=str(exc),
                        consecutive_failures=consecutive_failures,
                        **timing,
                    )
                    if consecutive_failures == 1:
                        print(f"EMERS cannot read {provider.source}: {exc}")

                    startup_failure = not self.ready_event.is_set()
                    if self.failure_policy == "fail" and (
                        startup_failure or consecutive_failures >= self.max_failures
                    ):
                        raise MeasurementUnavailableError(
                            f"Measurement provider {provider.source} is unavailable: {exc}"
                        ) from exc

                    should_fallback = (
                        self.failure_policy == "fallback"
                        and next_fallback_index < len(self.fallbacks)
                        and (startup_failure or consecutive_failures >= self.max_failures)
                    )
                    if should_fallback:
                        fallback, fallback_started, next_fallback_index = (
                            await self._activate_next_fallback(
                                provider,
                                started,
                                reason=str(exc),
                                start_index=next_fallback_index,
                            )
                        )
                        if fallback is not None:
                            provider = fallback
                            started = fallback_started
                            consecutive_failures = 0
                            self.ready_event.set()
                            await self._sleep_interruptibly(provider.polling_interval)
                            continue

                    # In non-strict modes the experiment may proceed, but the
                    # manifest and event stream make the measurement gap explicit.
                    self.ready_event.set()
                    delay = min(
                        self.retry_backoff * (2 ** (consecutive_failures - 1)),
                        self.max_retry_backoff,
                    )
                    await self._sleep_interruptibly(delay)
        finally:
            try:
                if started and provider is not None:
                    final_sample = await self._acquire(provider, provider.stop)
                    if final_sample is not None:
                        self._write_sample(final_sample)
            except Exception as exc:
                self._had_errors = True
                self.record_event(
                    "provider_stop_error",
                    provider=getattr(provider, "source", "unavailable"),
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
            if self._had_errors or self._fallback_active:
                final_status = "degraded"
            else:
                final_status = "complete"
            self._close_gap("measurement_stopped")
            self._finish_segment(
                "complete" if final_status == "complete" else "degraded"
            )
            self.update_manifest({
                "measurement": {
                    "status": final_status,
                    "active_segment": None,
                    "segments": list(self._segments),
                    "gaps": list(self._gaps),
                    "coverage": self._coverage(),
                },
                "measurement_ended_at": _utc_now(),
            })
            self.record_event(
                "measurement_stopped",
                status=final_status,
                recovered=recovered_from_error,
            )

    def _start_experiment_logging(self):
        if self.loop_thread is not None and self.loop_thread.is_alive():
            return
        self.stop_event.clear()
        self.ready_event.clear()
        self.background_error = None

        def async_intermediate():
            try:
                asyncio.run(self.log_data())
            except BaseException as exc:
                self.background_error = exc
            finally:
                self.ready_event.set()

        self.loop_thread = threading.Thread(
            target=async_intermediate,
            name=f"emers-{self.run_id}",
        )
        self.loop_thread.start()
        if not self.ready_event.wait(self.startup_timeout):
            self.stop_event.set()
            self.loop_thread.join()
            raise MeasurementUnavailableError(
                f"Measurement provider did not initialize within {self.startup_timeout:g}s"
            )
        if self.background_error is not None:
            error = self.background_error
            self.loop_thread.join()
            self.loop_thread = None
            raise error

        print(
            f"EMERS logging started for source {self.source_name}, "
            f"experiment {self.experiment_name} with polling rate {self.polling_rate:g}s."
        )

    def _finish_experiment_logging(self):
        if self.loop_thread is not None:
            self.stop_event.set()
            self.loop_thread.join()
            self.loop_thread = None
        print(
            f"EMERS logging stopped for source {self.source_name}, "
            f"experiment {self.experiment_name}."
        )

    @property
    def is_running(self):
        return self.loop_thread is not None and self.loop_thread.is_alive()

    def __enter__(self):
        self._start_experiment_logging()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._finish_experiment_logging()
        if exc_type is None and self.background_error is not None:
            raise self.background_error
