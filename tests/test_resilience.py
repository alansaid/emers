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

    def build_provider(fallback=False, settings=None, **kwargs):
        return fallback_provider if settings is not None or fallback else primary

    monkeypatch.setattr(manager, "_build_provider", build_provider)

    with manager:
        sleep(0.02)

    manifest = json.loads(manager.manifest_path.read_text())
    events = [json.loads(line) for line in manager.events_path.read_text().splitlines()]
    csv_file = next(manager.log_base.glob("*.csv"))
    assert manifest["measurement"]["status"] == "degraded"
    assert manifest["measurement"]["fallback_used"] is True
    assert any(event["event"] == "provider_switched" for event in events)
    assert "MockPlug,codecarbon,estimated,cpu_gpu_ram" in csv_file.read_text()


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
    primary.settings = {
        "provider": "tapo",
        "device_ip": "192.0.2.1",
        "tapo_user": "researcher@example.test",
        "tapo_password": "do-not-record",
        "calibration": {
            "reference_meter": "analyzer-1",
            "reference_uncertainty_percent": 0.5,
        },
    }
    fallback_provider = FallbackProvider()
    monkeypatch.setattr(
        manager,
        "_build_provider",
        lambda fallback=False, settings=None, **kwargs: (
            fallback_provider if settings is not None or fallback else primary
        ),
    )

    with manager:
        sleep(0.03)

    manifest = json.loads(manager.manifest_path.read_text())
    events = [json.loads(line) for line in manager.events_path.read_text().splitlines()]
    assert manifest["measurement"]["status"] == "degraded"
    assert any(
        event["event"] == "provider_switched"
        and "plug disconnected" in event["reason"]
        for event in events
    )
    assert len(manifest["measurement"]["segments"]) == 2
    assert [segment["scope"] for segment in manifest["measurement"]["segments"]] == [
        "whole_system_wall",
        "cpu_gpu_ram",
    ]
    csv_file = next(manager.log_base.glob("*.csv"))
    rows = csv_file.read_text().splitlines()
    assert "segment_id,segment_index" in rows[0]
    assert any("segment-001" in row for row in rows[1:])
    assert any("segment-002" in row for row in rows[1:])
    assert manifest["measurement"]["coverage"]["gap_count"] >= 1
    primary_segment = manifest["measurement"]["segments"][0]
    assert primary_segment["configuration"]["device_ip"] == "192.0.2.1"
    assert "tapo_password" not in primary_segment["configuration"]
    assert "tapo_user" not in primary_segment["configuration"]
    assert primary_segment["calibration"]["reference_uncertainty_percent"] == 0.5
    assert "tapo" in primary_segment["versions"]


def test_fallback_chain_skips_unavailable_provider(tmp_path, monkeypatch):
    manager = manager_for(
        tmp_path,
        failure_policy="fallback",
        fallbacks=[{"provider": "first"}, {"provider": "second"}],
    )
    primary = UnavailableProvider()
    unavailable_fallback = UnavailableProvider()
    successful_fallback = FallbackProvider()

    def build_provider(fallback=False, settings=None, **kwargs):
        if settings is None:
            return primary
        if settings["provider"] == "first":
            return unavailable_fallback
        return successful_fallback

    monkeypatch.setattr(manager, "_build_provider", build_provider)

    with manager:
        sleep(0.02)

    events = [json.loads(line) for line in manager.events_path.read_text().splitlines()]
    assert any(
        event["event"] == "fallback_unavailable" and event["fallback_index"] == 0
        for event in events
    )
    assert any(
        event["event"] == "provider_switched" and event["fallback_index"] == 1
        for event in events
    )


def test_fallback_chain_handles_primary_plugin_load_failure(tmp_path, monkeypatch):
    manager = manager_for(
        tmp_path,
        failure_policy="fallback",
        fallbacks=[{"provider": "working"}],
    )
    fallback_provider = FallbackProvider()

    def build_provider(fallback=False, settings=None, **kwargs):
        if settings is None:
            raise ImportError("primary plugin missing")
        return fallback_provider

    monkeypatch.setattr(manager, "_build_provider", build_provider)

    with manager:
        sleep(0.02)

    manifest = json.loads(manager.manifest_path.read_text())
    assert manifest["measurement"]["fallback_used"] is True
    assert manifest["measurement"]["segments"][0]["provider"] == "codecarbon"


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
