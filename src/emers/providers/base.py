"""Provider abstractions shared by EMERS measurement sources."""

from __future__ import annotations

import asyncio
import importlib
from dataclasses import asdict, dataclass, replace

from emers.measurement import MeasurementLogResult


class ProviderUnavailableError(RuntimeError):
    """Raised when a measurement provider cannot be started or queried."""


@dataclass(frozen=True)
class ProviderCapabilities:
    """Machine-readable description of a provider's measurement semantics."""

    provider: str
    method: str
    scope: str
    components: tuple[str, ...] = ()
    supports_periodic_readings: bool = True
    cumulative_energy_resolution_kwh: float | None = None

    def to_dict(self):
        payload = asdict(self)
        payload["components"] = list(self.components)
        return payload


class FunctionProvider:
    """Adapter for built-in asynchronous provider functions."""

    def __init__(self, provider_type, settings, timeout, polling_rate):
        self.provider_type = provider_type
        self.settings = settings
        self.timeout = timeout
        self.polling_interval = polling_rate
        self.source = provider_type
        self.method = "synthetic" if provider_type == "mock" else "measured"
        self.scope = "synthetic" if provider_type == "mock" else "whole_system_wall"
        self._api = None

    @property
    def capabilities(self):
        components = () if self.provider_type == "mock" else ("whole_system",)
        return ProviderCapabilities(
            provider=self.source,
            method=self.method,
            scope=self.scope,
            components=components,
            cumulative_energy_resolution_kwh=(
                float(self.settings.get("cumulative_energy_resolution_kwh", 0.001))
                if self.provider_type == "tapo"
                else self.settings.get("cumulative_energy_resolution_kwh")
            ),
        )

    async def start(self):
        module_name = f"emers.providers.{self.provider_type}"
        function_name = f"get_data_{self.provider_type}"
        try:
            module = importlib.import_module(module_name)
            self._api = getattr(module, function_name)
        except (ImportError, AttributeError) as exc:
            raise ProviderUnavailableError(
                f"Could not load provider implementation {module_name}: {exc}"
            ) from exc
        return await self.read()

    async def read(self):
        try:
            settings = dict(self.settings)
            settings.setdefault("request_timeout", self.timeout)
            result = await asyncio.wait_for(self._api(**settings), self.timeout)
        except asyncio.TimeoutError as exc:
            raise ProviderUnavailableError(
                f"{self.provider_type} did not respond within {self.timeout:g}s"
            ) from exc
        if not isinstance(result, MeasurementLogResult):
            raise ProviderUnavailableError(
                f"{self.provider_type} returned {type(result).__name__}, "
                "expected MeasurementLogResult"
            )
        return replace(
            result,
            source=self.source,
            measurement_method=self.method,
            measurement_scope=self.scope,
        )

    async def stop(self):
        return None
