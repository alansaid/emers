"""Measurement providers used by the EMERS run lifecycle."""

from __future__ import annotations

import asyncio
import importlib
from dataclasses import replace
from time import time

from emers.measurement import MeasurementLogResult


class ProviderUnavailableError(RuntimeError):
    """Raised when a measurement provider cannot be started or queried."""


class FunctionProvider:
    """Adapter for the built-in async smart-plug functions."""

    def __init__(self, device_type, settings, timeout, polling_rate):
        self.device_type = device_type
        self.settings = settings
        self.timeout = timeout
        self.polling_interval = polling_rate
        self.source = device_type
        self.method = "synthetic" if device_type == "mock" else "measured"
        self.scope = "synthetic" if device_type == "mock" else "whole_system_wall"
        self._api = None

    async def start(self):
        module_name = f"emers.meters.{self.device_type}"
        function_name = f"get_data_{self.device_type}"
        try:
            module = importlib.import_module(module_name)
            self._api = getattr(module, function_name)
        except (ImportError, AttributeError) as exc:
            raise ProviderUnavailableError(
                f"Could not load meter implementation {module_name}: {exc}"
            ) from exc
        return await self.read()

    async def read(self):
        try:
            settings = dict(self.settings)
            settings.setdefault("request_timeout", self.timeout)
            result = await asyncio.wait_for(self._api(**settings), self.timeout)
        except asyncio.TimeoutError as exc:
            raise ProviderUnavailableError(
                f"{self.device_type} did not respond within {self.timeout:g}s"
            ) from exc
        if not isinstance(result, MeasurementLogResult):
            raise ProviderUnavailableError(
                f"{self.device_type} returned {type(result).__name__}, expected MeasurementLogResult"
            )
        return replace(
            result,
            source=self.source,
            measurement_method=self.method,
            measurement_scope=self.scope,
        )

    async def stop(self):
        return None


class CodeCarbonProvider:
    """CodeCarbon-backed software estimate provider.

    CodeCarbon task measurements are deliberately sampled at a slower interval
    than smart plugs. Each task result supplies energy for one interval; EMERS
    accumulates those intervals into a monotonically increasing kWh value.
    """

    source = "codecarbon"
    method = "estimated"
    scope = "cpu_gpu_ram"

    def __init__(self, settings, polling_rate, tracker_factory=None):
        self.settings = settings
        self.polling_interval = max(
            float(settings.get("measure_power_secs", 15)), float(polling_rate)
        )
        self._tracker_factory = tracker_factory
        self._tracker = None
        self._task_active = False
        self._sequence = 0
        self._total_kwh = 0.0

    def _make_tracker(self):
        if self._tracker_factory is None:
            try:
                from codecarbon import EmissionsTracker, OfflineEmissionsTracker
            except ImportError as exc:
                raise ProviderUnavailableError(
                    "CodeCarbon is not installed. Install EMERS with "
                    "'pip install emers[codecarbon]'."
                ) from exc
            if self.settings.get("country_iso_code"):
                self._tracker_factory = OfflineEmissionsTracker
            else:
                self._tracker_factory = EmissionsTracker

        kwargs = {
            "project_name": self.settings.get("project_name", "emers"),
            "experiment_name": self.settings.get("experiment_name", "measurement"),
            "measure_power_secs": self.polling_interval,
            "tracking_mode": self.settings.get("tracking_mode", "machine"),
            "save_to_file": False,
            "save_to_api": False,
            "log_level": self.settings.get("log_level", "warning"),
            "allow_multiple_runs": True,
        }
        if self.settings.get("country_iso_code"):
            kwargs["country_iso_code"] = self.settings["country_iso_code"]
        return self._tracker_factory(**kwargs)

    def _task_name(self):
        self._sequence += 1
        return f"emers_interval_{self._sequence}"

    async def start(self):
        self._tracker = await asyncio.to_thread(self._make_tracker)
        await asyncio.to_thread(self._tracker.start_task, self._task_name())
        self._task_active = True
        return None

    def _result_to_sample(self, data):
        if data is None:
            raise ProviderUnavailableError("CodeCarbon did not return interval data")
        energy_kwh = float(data.energy_consumed or 0.0)
        duration = float(data.duration or self.polling_interval)
        self._total_kwh += energy_kwh
        power_watts = energy_kwh * 3_600_000 / duration if duration > 0 else 0.0
        detail_fields = (
            "cpu_energy",
            "gpu_energy",
            "ram_energy",
            "cpu_power",
            "gpu_power",
            "ram_power",
            "emissions_rate",
            "country_name",
            "country_iso_code",
            "region",
            "cloud_provider",
            "cloud_region",
            "tracking_mode",
        )
        details = {
            field: getattr(data, field)
            for field in detail_fields
            if getattr(data, field, None) is not None
        }
        details.update({
            "emissions_kg": float(data.emissions or 0.0),
            "duration_seconds": duration,
        })
        return MeasurementLogResult(
            timestamp=time(),
            current_draw=power_watts,
            total_draw=self._total_kwh,
            misc=details,
            source=self.source,
            measurement_method=self.method,
            measurement_scope=self.scope,
        )

    async def read(self):
        data = await asyncio.to_thread(self._tracker.stop_task)
        self._task_active = False
        sample = self._result_to_sample(data)
        await asyncio.to_thread(self._tracker.start_task, self._task_name())
        self._task_active = True
        return sample

    async def stop(self):
        if not self._task_active:
            return None
        data = await asyncio.to_thread(self._tracker.stop_task)
        self._task_active = False
        return self._result_to_sample(data)


def build_provider(device, *, timeout, polling_rate, tracker_factory=None):
    device_type = device.get("device_type")
    if not device_type:
        raise ProviderUnavailableError("Device configuration has no 'device_type'")
    if device_type == "codecarbon":
        return CodeCarbonProvider(device, polling_rate, tracker_factory=tracker_factory)
    return FunctionProvider(device_type, device, timeout, polling_rate)
