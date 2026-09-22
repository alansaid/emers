import json
from time import sleep, time

import pytest

from emers.cli import init_workspace
from emers.measurement import (
    MeasurementLogResult,
    MeasurementManager,
    MeasurementUnavailableError,
)


class UnavailableProvider:
    source = "tapo"
    method = "measured"
    scope = "whole_system_wall"
    polling_interval = 0.001

    async def start(self):
        raise ConnectionError("plug offline")

    async def read(self):
        raise ConnectionError("plug offline")

    async def stop(self):
        return None


class FallbackProvider:
    source = "codecarbon"
    method = "estimated"
    scope = "cpu_gpu_ram"
    polling_interval = 0.001

    def __init__(self):
        self.total = 0.0

    async def start(self):
        return None

    async def read(self):
        self.total += 0.001
        return MeasurementLogResult(
            time(),
            10.0,
            self.total,
            source=self.source,
            measurement_method=self.method,
            measurement_scope=self.scope,
        )

    async def stop(self):
        return None


def manager_for(tmp_path, **kwargs):
    init_workspace(tmp_path)
    return MeasurementManager(
        "MockPlug",
        experiment_name="resilience",
        workspace=tmp_path,
        polling_rate=0.001,
        retry_backoff=0.001,
        max_retry_backoff=0.002,
        **kwargs,
    )


def test_fail_policy_rejects_unavailable_provider_before_experiment(tmp_path, monkeypatch):
    manager = manager_for(tmp_path, failure_policy="fail")
    monkeypatch.setattr(manager, "_build_provider", lambda fallback=False: UnavailableProvider())

    with pytest.raises(MeasurementUnavailableError, match="plug offline"):
        manager.__enter__()

    manifest = json.loads(manager.manifest_path.read_text())
    assert manifest["measurement"]["status"] == "degraded"


def test_fallback_policy_labels_estimated_samples_and_events(tmp_path, monkeypatch):
    manager = manager_for(tmp_path, failure_policy="fallback")
    primary = UnavailableProvider()
    fallback_provider = FallbackProvider()

    def build_provider(fallback=False):
        return fallback_provider if fallback else primary

    monkeypatch.setattr(manager, "_build_provider", build_provider)

    with manager:
        sleep(0.02)

    manifest = json.loads(manager.manifest_path.read_text())
    events = [json.loads(line) for line in manager.events_path.read_text().splitlines()]
    csv_file = next(manager.log_base.glob("*.csv"))
    assert manifest["measurement"]["status"] == "estimated"
    assert manifest["measurement"]["fallback_used"] is True
    assert any(event["event"] == "provider_switched" for event in events)
    assert "codecarbon,estimated,cpu_gpu_ram" in csv_file.read_text()


def test_fallback_policy_switches_after_runtime_outage(tmp_path, monkeypatch):
    manager = manager_for(tmp_path, failure_policy="fallback", max_failures=2)

    class FailsAfterStartup(FallbackProvider):
        source = "tapo"
        method = "measured"
        scope = "whole_system_wall"

        async def start(self):
            return await self.read()

        async def read(self):
            if self.total:
                raise ConnectionError("plug disconnected")
            self.total = 0.001
            return MeasurementLogResult(time(), 10.0, self.total)

    primary = FailsAfterStartup()
    fallback_provider = FallbackProvider()
    monkeypatch.setattr(
        manager,
        "_build_provider",
        lambda fallback=False: fallback_provider if fallback else primary,
    )

    with manager:
        sleep(0.03)

    manifest = json.loads(manager.manifest_path.read_text())
    events = [json.loads(line) for line in manager.events_path.read_text().splitlines()]
    assert manifest["measurement"]["status"] == "estimated"
    assert any(
        event["event"] == "provider_switched"
        and "plug disconnected" in event["reason"]
        for event in events
    )


def test_continue_policy_recovers_after_a_measurement_gap(tmp_path, monkeypatch):
    manager = manager_for(tmp_path, failure_policy="continue")

    class FlakyProvider(FallbackProvider):
        source = "shelly"
        method = "measured"
        scope = "whole_system_wall"

        def __init__(self):
            super().__init__()
            self.attempts = 0

        async def start(self):
            self.attempts += 1
            if self.attempts == 1:
                raise ConnectionError("temporary outage")
            return await self.read()

    provider = FlakyProvider()
    monkeypatch.setattr(
        manager,
        "_build_provider",
        lambda fallback=False: provider,
    )

    with manager:
        sleep(0.02)

    manifest = json.loads(manager.manifest_path.read_text())
    events = [json.loads(line) for line in manager.events_path.read_text().splitlines()]
    assert manifest["measurement"]["status"] == "degraded"
    assert any(event["event"] == "provider_recovered" for event in events)
