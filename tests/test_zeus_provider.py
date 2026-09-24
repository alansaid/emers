import asyncio
from types import SimpleNamespace

import pytest

from emers.providers.base import ProviderUnavailableError
from emers.providers.zeus import ZeusProvider


class FakeZeusMonitor:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.gpu_indices = [0]
        self.cpu_indices = [0]
        self.soc_is_present = False
        self.windows = []

    def begin_window(self, name, sync_execution=True):
        self.windows.append(name)

    def end_window(self, name, sync_execution=True):
        assert name in self.windows
        return SimpleNamespace(
            time=2.0,
            gpu_energy={0: 20.0},
            cpu_energy={0: 10.0},
            dram_energy={0: 5.0},
            soc_energy=None,
        )


def test_zeus_provider_normalizes_hardware_counter_energy():
    provider = ZeusProvider(
        {"provider": "zeus", "measure_power_secs": 1},
        polling_rate=0.5,
        monitor_factory=FakeZeusMonitor,
    )

    async def collect():
        assert await provider.start() is None
        sample = await provider.read()
        await provider.stop()
        return sample

    sample = asyncio.run(collect())

    assert sample.current_draw == pytest.approx(17.5)
    assert sample.total_draw == pytest.approx(35 / 3_600_000)
    assert sample.measurement_method == "measured"
    assert sample.measurement_scope == "hardware_components"
    assert provider.capabilities.components == ("gpu", "cpu", "dram")


def test_zeus_provider_rejects_host_without_supported_counters():
    class EmptyMonitor(FakeZeusMonitor):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.gpu_indices = []
            self.cpu_indices = []

    provider = ZeusProvider(
        {"provider": "zeus"}, polling_rate=1, monitor_factory=EmptyMonitor
    )

    with pytest.raises(ProviderUnavailableError, match="no supported"):
        asyncio.run(provider.start())
