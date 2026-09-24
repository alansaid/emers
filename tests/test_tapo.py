from types import SimpleNamespace

import pytest

from emers.providers.tapo import _energy_kwh
from emers.meters.tapo import _energy_kwh as legacy_energy_kwh


def test_legacy_meter_module_reexports_provider():
    assert legacy_energy_kwh is _energy_kwh


def test_energy_kwh_from_current_tapo_response():
    result = SimpleNamespace(entries=[
        SimpleNamespace(energy=1250),
        SimpleNamespace(energy=None),
        SimpleNamespace(energy=750),
    ])

    assert _energy_kwh(result) == 2.0


def test_energy_kwh_from_legacy_tapo_response():
    result = SimpleNamespace(data=[1250, 750])

    assert _energy_kwh(result) == 2.0


def test_energy_kwh_rejects_unknown_response():
    with pytest.raises(TypeError, match="Unsupported tapo energy response"):
        _energy_kwh(SimpleNamespace())
