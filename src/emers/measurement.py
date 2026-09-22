"""Resilient measurement collection and run artifact recording."""

from __future__ import annotations

import asyncio
import csv
import json
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import time
from typing import Optional


CSV_FIELDS = (
    "timestamp",
    "current_draw",
    "total_draw",
    "source",
    "measurement_method",
    "measurement_scope",
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


class MeasurementUnavailableError(RuntimeError):
    """Raised when the configured measurement policy cannot continue."""


class MeasurementManager:
    """Collect and persist measurements with explicit outage handling.

    ``failure_policy`` controls provider failures:

    - ``fallback`` (default): switch permanently to CodeCarbon after the
      primary provider is unavailable. If CodeCarbon is not installed, retain
      the primary provider and keep retrying while recording the gap.
    - ``continue``: keep retrying the primary provider with exponential
      backoff. The experiment continues and the run is marked degraded.
    - ``fail``: fail startup immediately or stop collection after
      ``max_failures`` consecutive runtime failures. Background failures are
      raised when the context exits.
    """

    def __init__(
        self,
        device_name,
        experiment_name=None,
        polling_rate=0.5,
        log_interval=300,
        workspace=None,
        config=None,
        *,
        failure_policy="fallback",
        fallback_device="codecarbon",
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
        self.device_name = device_name
        self.experiment_name = experiment_name or "continuous"
        self.polling_rate = float(polling_rate)
        self.log_interval = int(log_interval)
        self.failure_policy = failure_policy
        self.fallback_device = fallback_device
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
        self._last_output_total = None
        self._provider_input_base = None
        self._provider_output_base = None
        self._log_started_at = None
        self._log_file = None

        self.config_path = Path(config or "settings.json").expanduser()
        if not self.config_path.is_absolute():
            self.config_path = self.workspace / self.config_path
        try:
            with self.config_path.open("r", encoding="utf-8") as file:
                self.devices = json.load(file)
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"Configuration not found at {self.config_path}. Run 'emers init' first."
            ) from exc

        if self.device_name not in self.devices:
            raise ValueError(f"Device {self.device_name} not found in {self.config_path}")
        self.device = self.devices[self.device_name]

        self.log_base = (
            self.workspace / "measurements" / self.device_name / self.experiment_name
        )
        self.artifact_dir = self.log_base / ".emers" / "runs" / self.run_id
        self.manifest_path = self.artifact_dir / "run.json"
        self.events_path = self.artifact_dir / "events.jsonl"
        self.manifest = {
            "schema_version": 1,
            "run_id": self.run_id,
            "experiment": self.experiment_name,
            "device": self.device_name,
            "created_at": _utc_now(),
            "status": "initialized",
            "metadata": metadata or {},
            "parameters": {},
            "metrics": {},
            "measurement": {
                "primary_provider": self.device.get("device_type", "unknown"),
                "active_provider": None,
                "method": None,
                "scope": None,
                "failure_policy": self.failure_policy,
                "status": "initializing",
                "error_count": 0,
                "fallback_used": False,
            },
        }

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
            "timestamp": time(),
            "timestamp_iso": _utc_now(),
            "event": event,
            **details,
        }
        with self._artifact_lock:
            with self.events_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(payload, sort_keys=True, default=str) + "\n")

    def _provider_settings(self, fallback=False):
        if not fallback:
            return self.device
        configured = self.fallback_device
        if isinstance(configured, dict):
            return configured
        if isinstance(configured, str) and configured in self.devices:
            return self.devices[configured]
        if configured in {None, "codecarbon"}:
            return {
                "device_type": "codecarbon",
                "project_name": "emers",
                "experiment_name": self.experiment_name,
            }
        raise ValueError(f"Fallback device {configured!r} not found in {self.config_path}")

    def _build_provider(self, fallback=False):
        # Import lazily to avoid a module cycle: providers normalize results
        # using MeasurementLogResult from this module.
        from emers.providers import build_provider

        return build_provider(
            self._provider_settings(fallback=fallback),
            timeout=self.request_timeout,
            polling_rate=self.polling_rate,
            tracker_factory=self._tracker_factory,
        )

    def _set_active_provider(self, provider, fallback=False):
        self._provider_input_base = None
        self._provider_output_base = self._last_output_total
        self.update_manifest({
            "measurement": {
                "active_provider": provider.source,
                "method": provider.method,
                "scope": provider.scope,
                "status": "estimated" if fallback else "healthy",
                "fallback_used": fallback,
            }
        })

    def _normalize_total(self, result):
        if self._provider_output_base is None:
            self._last_output_total = result.total_draw
            return result.total_draw
        if self._provider_input_base is None:
            self._provider_input_base = result.total_draw
        normalized = self._provider_output_base + (result.total_draw - self._provider_input_base)
        self._last_output_total = normalized
        return normalized

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
        row["total_draw"] = self._normalize_total(result)
        row["run_id"] = self.run_id
        row["misc"] = json.dumps(result.misc or {}, sort_keys=True, default=str)
        with self._log_file.open("a", newline="", encoding="utf-8") as file:
            csv.DictWriter(file, fieldnames=CSV_FIELDS).writerow(row)

    async def _sleep_interruptibly(self, seconds):
        deadline = asyncio.get_running_loop().time() + max(0.0, seconds)
        while not self.stop_event.is_set():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, 0.25))

    async def _activate_fallback(self, provider, started, reason):
        if provider.source == "codecarbon":
            return None
        if started:
            try:
                final_sample = await provider.stop()
                if final_sample is not None:
                    self._write_sample(final_sample)
            except Exception as exc:  # provider is already degraded
                self.record_event("provider_stop_error", provider=provider.source, error=str(exc))

        fallback = None
        try:
            fallback = self._build_provider(fallback=True)
            initial = await fallback.start()
        except Exception as exc:
            self.record_event(
                "fallback_unavailable",
                provider=getattr(fallback, "source", "unknown"),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            print(f"EMERS fallback unavailable: {exc}. Continuing to retry {provider.source}.")
            return None

        self._fallback_active = True
        self._set_active_provider(fallback, fallback=True)
        self.record_event(
            "provider_switched",
            from_provider=provider.source,
            to_provider=fallback.source,
            reason=reason,
            method=fallback.method,
            scope=fallback.scope,
        )
        print(
            f"EMERS switched from {provider.source} to {fallback.source}; subsequent values "
            f"are {fallback.method} and are labelled accordingly."
        )
        if initial is not None:
            self._write_sample(initial)
        return fallback

    async def log_data(self):
        """Collect measurements until ``stop_event`` is set."""
        self._ensure_artifacts()
        self.update_manifest({"status": "running", "started_at": _utc_now()})
        self.record_event(
            "measurement_started",
            device=self.device_name,
            provider=self.device.get("device_type"),
            failure_policy=self.failure_policy,
        )

        provider = self._build_provider()
        started = False
        consecutive_failures = 0
        fallback_attempted = False
        recovered_from_error = False

        try:
            while not self.stop_event.is_set():
                try:
                    result = await (provider.read() if started else provider.start())
                    started = True
                    if self.manifest["measurement"]["active_provider"] != provider.source:
                        self._set_active_provider(provider, fallback=self._fallback_active)
                    if consecutive_failures:
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
                    )
                    if consecutive_failures == 1:
                        print(f"EMERS cannot read {provider.source}: {exc}")

                    startup_failure = not self.ready_event.is_set()
                    if startup_failure and provider.source == "codecarbon":
                        raise MeasurementUnavailableError(
                            f"CodeCarbon could not start: {exc}"
                        ) from exc
                    if self.failure_policy == "fail" and (
                        startup_failure or consecutive_failures >= self.max_failures
                    ):
                        raise MeasurementUnavailableError(
                            f"Measurement provider {provider.source} is unavailable: {exc}"
                        ) from exc

                    should_fallback = (
                        self.failure_policy == "fallback"
                        and not self._fallback_active
                        and not fallback_attempted
                        and (startup_failure or consecutive_failures >= self.max_failures)
                    )
                    if should_fallback:
                        fallback_attempted = True
                        fallback = await self._activate_fallback(
                            provider, started, reason=str(exc)
                        )
                        if fallback is not None:
                            provider = fallback
                            started = True
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
                if started:
                    final_sample = await provider.stop()
                    if final_sample is not None:
                        self._write_sample(final_sample)
            except Exception as exc:
                self._had_errors = True
                self.record_event(
                    "provider_stop_error",
                    provider=provider.source,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
            if self._fallback_active and provider.method == "estimated":
                final_status = "estimated"
            elif self._had_errors or self._fallback_active:
                final_status = "degraded"
            else:
                final_status = "complete"
            self.update_manifest({
                "measurement": {"status": final_status},
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
            f"EMERS logging started for device {self.device_name}, "
            f"experiment {self.experiment_name} with polling rate {self.polling_rate:g}s."
        )

    def _finish_experiment_logging(self):
        if self.loop_thread is not None:
            self.stop_event.set()
            self.loop_thread.join()
            self.loop_thread = None
        print(
            f"EMERS logging stopped for device {self.device_name}, "
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
