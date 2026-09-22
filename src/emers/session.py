"""Public in-process experiment run API."""

from __future__ import annotations

import functools
import platform
import socket
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from emers.measurement import MeasurementManager


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "item") and callable(value.item):
        try:
            return _json_safe(value.item())
        except (TypeError, ValueError):
            pass
    return repr(value)


def _git_metadata(workspace):
    def command(*args):
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=workspace,
                capture_output=True,
                check=True,
                text=True,
                timeout=2,
            )
            return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None

    commit = command("rev-parse", "HEAD")
    if not commit:
        return None
    return {
        "commit": commit,
        "branch": command("branch", "--show-current"),
        "dirty": bool(command("status", "--porcelain")),
    }


class Run:
    """An instrumented experiment run.

    Runs are context managers. They capture lifecycle state and structured
    metadata while a :class:`MeasurementManager` records energy in the
    background.
    """

    def __init__(
        self,
        name,
        *,
        device,
        workspace=None,
        config=None,
        polling_rate=0.5,
        log_interval=300,
        failure_policy="fallback",
        fallback_device="codecarbon",
        max_failures=3,
        request_timeout=10.0,
        retry_backoff=1.0,
        max_retry_backoff=30.0,
        metadata=None,
        parameters=None,
        tags=None,
        _tracker_factory=None,
    ):
        self.name = name
        self.workspace = Path(workspace or ".").expanduser().resolve()
        self._entered = False
        self._started_at = None
        self._phases = []
        runtime_metadata = {
            "host": socket.gethostname(),
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "executable": sys.executable,
        }
        git = _git_metadata(self.workspace)
        if git:
            runtime_metadata["git"] = git
        if metadata:
            runtime_metadata.update(_json_safe(metadata))

        self.manager = MeasurementManager(
            device_name=device,
            experiment_name=name,
            polling_rate=polling_rate,
            log_interval=log_interval,
            workspace=self.workspace,
            config=config,
            failure_policy=failure_policy,
            fallback_device=fallback_device,
            max_failures=max_failures,
            request_timeout=request_timeout,
            retry_backoff=retry_backoff,
            max_retry_backoff=max_retry_backoff,
            metadata=runtime_metadata,
            tracker_factory=_tracker_factory,
        )
        self.run_id = self.manager.run_id
        self.manifest_path = self.manager.manifest_path
        self._initial_parameters = _json_safe(parameters or {})
        self._initial_tags = list(tags or [])

    def __enter__(self):
        self._started_at = monotonic()
        self.manager.update_manifest({
            "status": "starting",
            "run_started_at": _utc_now(),
            "parameters": self._initial_parameters,
            "tags": self._initial_tags,
            "phases": [],
        })
        self.manager.record_event("run_starting", name=self.name)
        try:
            self.manager.__enter__()
        except BaseException as exc:
            self.manager.update_manifest({
                "status": "failed_to_start",
                "run_ended_at": _utc_now(),
                "error": {"type": type(exc).__name__, "message": str(exc)},
            })
            self.manager.record_event(
                "run_failed_to_start", error_type=type(exc).__name__, error=str(exc)
            )
            raise
        self._entered = True
        self.manager.update_manifest({"status": "running"})
        self.manager.record_event("run_started", name=self.name)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        measurement_error = None
        try:
            self.manager.__exit__(exc_type, exc_value, traceback)
        except BaseException as exc:
            measurement_error = exc

        duration = monotonic() - self._started_at if self._started_at else None
        if exc_type is not None:
            status = "failed"
            error = {"type": exc_type.__name__, "message": str(exc_value)}
        elif measurement_error is not None:
            status = "completed_with_measurement_error"
            error = {
                "type": type(measurement_error).__name__,
                "message": str(measurement_error),
            }
        elif self.manager.manifest["measurement"]["status"] in {"degraded", "estimated"}:
            status = "completed_with_partial_measurement"
            error = None
        else:
            status = "completed"
            error = None

        changes = {
            "status": status,
            "run_ended_at": _utc_now(),
            "duration_seconds": duration,
        }
        if error:
            changes["error"] = error
        self.manager.update_manifest(changes)
        self.manager.record_event("run_finished", status=status, duration_seconds=duration)
        self._entered = False

        if exc_type is None and measurement_error is not None:
            raise measurement_error
        return False

    def log_parameters(self, values=None, **kwargs):
        payload = dict(values or {})
        payload.update(kwargs)
        payload = _json_safe(payload)
        parameters = dict(self.manager.manifest.get("parameters", {}))
        parameters.update(payload)
        self.manager.update_manifest({"parameters": parameters})
        self.manager.record_event("parameters_logged", values=payload)
        return self

    log_params = log_parameters

    def log_metrics(self, values=None, *, step=None, **kwargs):
        payload = dict(values or {})
        payload.update(kwargs)
        payload = _json_safe(payload)
        metrics = dict(self.manager.manifest.get("metrics", {}))
        metrics.update(payload)
        self.manager.update_manifest({"metrics": metrics})
        self.manager.record_event("metrics_logged", values=payload, step=step)
        return self

    def log_metric(self, name, value, *, step=None):
        return self.log_metrics({name: value}, step=step)

    def log_event(self, name, **attributes):
        self.manager.record_event("user_event", name=name, attributes=_json_safe(attributes))
        return self

    def set_tags(self, *tags):
        existing = list(self.manager.manifest.get("tags", []))
        for tag in tags:
            if tag not in existing:
                existing.append(str(tag))
        self.manager.update_manifest({"tags": existing})
        self.manager.record_event("tags_updated", tags=existing)
        return self

    @contextmanager
    def phase(self, name, **metadata):
        started_at = _utc_now()
        started = monotonic()
        self.manager.record_event(
            "phase_started", name=name, metadata=_json_safe(metadata)
        )
        try:
            yield self
        except BaseException as exc:
            status = "failed"
            error = {"type": type(exc).__name__, "message": str(exc)}
            raise
        else:
            status = "completed"
            error = None
        finally:
            phase = {
                "name": name,
                "started_at": started_at,
                "ended_at": _utc_now(),
                "duration_seconds": monotonic() - started,
                "status": status,
                "metadata": _json_safe(metadata),
            }
            if error:
                phase["error"] = error
            self._phases.append(phase)
            self.manager.update_manifest({"phases": list(self._phases)})
            self.manager.record_event(
                "phase_finished",
                name=name,
                status=status,
                duration_seconds=phase["duration_seconds"],
            )


def run(name, **kwargs):
    """Create an instrumented :class:`Run` context manager."""
    return Run(name, **kwargs)


def track(name=None, **run_kwargs):
    """Decorate a function so each invocation becomes an EMERS run."""
    def decorator(function):
        @functools.wraps(function)
        def wrapper(*args, **kwargs):
            run_name = name or function.__name__
            with Run(run_name, **run_kwargs):
                return function(*args, **kwargs)

        return wrapper

    return decorator
