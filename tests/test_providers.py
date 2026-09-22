import asyncio
from types import SimpleNamespace

import pytest

from emers.providers import CodeCarbonProvider, ProviderUnavailableError


class FakeTracker:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.active = False

    def start_task(self, name):
        self.active = True

    def stop_task(self):
        assert self.active
        self.active = False
        return SimpleNamespace(
            energy_consumed=0.001,
            duration=10,
            emissions=0.0002,
        )


def test_codecarbon_provider_normalizes_interval_results():
    provider = CodeCarbonProvider(
        {"device_type": "codecarbon", "measure_power_secs": 10},
        polling_rate=0.5,
        tracker_factory=FakeTracker,
    )

    async def collect():
        assert await provider.start() is None
        first = await provider.read()
        final = await provider.stop()
        return first, final

    first, final = asyncio.run(collect())

    assert first.current_draw == pytest.approx(360)
    assert first.total_draw == pytest.approx(0.001)
    assert final.total_draw == pytest.approx(0.002)
    assert first.measurement_method == "estimated"
    assert first.measurement_scope == "cpu_gpu_ram"


def test_codecarbon_provider_passes_offline_country_configuration():
    provider = CodeCarbonProvider(
        {
            "device_type": "codecarbon",
            "country_iso_code": "SWE",
            "tracking_mode": "process",
        },
        polling_rate=1,
        tracker_factory=FakeTracker,
    )

    asyncio.run(provider.start())

    assert provider._tracker.kwargs["country_iso_code"] == "SWE"
    assert provider._tracker.kwargs["tracking_mode"] == "process"
    asyncio.run(provider.stop())


def test_codecarbon_provider_explains_missing_extra(monkeypatch):
    provider = CodeCarbonProvider(
        {"device_type": "codecarbon"}, polling_rate=0.5
    )

    real_import = __import__

    def reject_codecarbon(name, *args, **kwargs):
        if name == "codecarbon":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", reject_codecarbon)

    with pytest.raises(ProviderUnavailableError, match=r"emers\[codecarbon\]"):
        asyncio.run(provider.start())
