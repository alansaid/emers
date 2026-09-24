"""Zeus hardware-counter energy provider."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
from time import time

from emers.measurement import MeasurementLogResult
from emers.providers.base import ProviderCapabilities, ProviderUnavailableError


class ZeusProvider:
    """Measure component energy using counters supported by Zeus."""

    source = "zeus"
    method = "measured"
    scope = "hardware_components"

    def __init__(self, settings, polling_rate, monitor_factory=None):
        self.settings = settings
        self.polling_interval = max(
            float(settings.get("measure_power_secs", 1)), float(polling_rate)
        )
        self._monitor_factory = monitor_factory
        self._monitor = None
        self._window = None
        self._sequence = 0
        self._total_kwh = 0.0
        self._components = ()

    @property
    def capabilities(self):
        return ProviderCapabilities(
            provider=self.source,
            method=self.method,
            scope=self.scope,
            components=tuple(self._components),
        )

    def _make_monitor(self):
        if self._monitor_factory is None:
            try:
                from zeus.monitor import ZeusMonitor
            except ImportError as exc:
                raise ProviderUnavailableError(
                    "Zeus is not installed. Install EMERS with "
                    "'pip install emers[zeus]'."
                ) from exc
            self._monitor_factory = ZeusMonitor

        kwargs = {
            "gpu_indices": self.settings.get("gpu_indices"),
            "cpu_indices": self.settings.get("cpu_indices"),
            "approx_instant_energy": self.settings.get(
                "approx_instant_energy", False
            ),
            "sync_execution_with": self.settings.get(
                "sync_execution_with", "torch"
            ),
        }
        monitor = self._monitor_factory(**kwargs)
        components = []
        if getattr(monitor, "gpu_indices", []):
            components.append("gpu")
        if getattr(monitor, "cpu_indices", []):
            components.extend(("cpu", "dram"))
        if getattr(monitor, "soc_is_present", False):
            components.append("soc")
        if not components:
            raise ProviderUnavailableError(
                "Zeus found no supported GPU, CPU/RAPL, or SoC energy counters"
            )
        self._components = tuple(dict.fromkeys(components))
        return monitor

    def _window_name(self):
        self._sequence += 1
        return f"emers_interval_{self._sequence}"

    def _begin_window(self):
        self._window = self._window_name()
        self._monitor.begin_window(
            self._window,
            sync_execution=self.settings.get("sync_execution", True),
        )

    async def start(self):
        self._monitor = await asyncio.to_thread(self._make_monitor)
        await asyncio.to_thread(self._begin_window)
        return None

    @staticmethod
    def _energy_map(value):
        return {str(key): float(item) for key, item in (value or {}).items()}

    @staticmethod
    def _soc_details(value):
        if value is None:
            return {}, 0.0
        if is_dataclass(value):
            details = asdict(value)
        elif hasattr(value, "__dict__"):
            details = dict(vars(value))
        else:
            details = {"value": repr(value)}
        total_mj = details.get("total_energy_mj")
        if total_mj is None:
            total_mj = sum(
                float(item)
                for key, item in details.items()
                if key.endswith("_energy_mj") and item is not None
            )
        return details, float(total_mj or 0.0) / 1000

    def _result_to_sample(self, result):
        if result is None:
            raise ProviderUnavailableError("Zeus did not return interval data")
        gpu = self._energy_map(getattr(result, "gpu_energy", None))
        cpu = self._energy_map(getattr(result, "cpu_energy", None))
        dram = self._energy_map(getattr(result, "dram_energy", None))
        soc, soc_joules = self._soc_details(getattr(result, "soc_energy", None))
        joules = sum(gpu.values()) + sum(cpu.values()) + sum(dram.values()) + soc_joules
        duration = float(getattr(result, "time", 0.0) or self.polling_interval)
        interval_kwh = joules / 3_600_000
        self._total_kwh += interval_kwh
        return MeasurementLogResult(
            timestamp=time(),
            current_draw=joules / duration if duration > 0 else 0.0,
            total_draw=self._total_kwh,
            misc={
                "duration_seconds": duration,
                "energy_joules": {
                    "gpu": gpu,
                    "cpu": cpu,
                    "dram": dram,
                    "soc": soc,
                },
                "components": list(self._components),
            },
            source=self.source,
            measurement_method=self.method,
            measurement_scope=self.scope,
        )

    async def read(self):
        result = await asyncio.to_thread(
            self._monitor.end_window,
            self._window,
            sync_execution=self.settings.get("sync_execution", True),
        )
        sample = self._result_to_sample(result)
        await asyncio.to_thread(self._begin_window)
        return sample

    async def stop(self):
        if self._window is None:
            return None
        result = await asyncio.to_thread(
            self._monitor.end_window,
            self._window,
            sync_execution=self.settings.get("sync_execution", True),
        )
        self._window = None
        return self._result_to_sample(result)
