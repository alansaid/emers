"""TP-Link Tapo P115 provider."""

from datetime import datetime
from time import time

from tapo import ApiClient
from tapo.requests import EnergyDataInterval

from emers.measurement import MeasurementLogResult


def _energy_kwh(result):
    """Return total energy in kWh across old and current tapo responses."""
    if hasattr(result, "entries"):
        values = (entry.energy for entry in result.entries)
    elif hasattr(result, "data"):
        values = result.data
    else:
        raise TypeError(
            "Unsupported tapo energy response: expected an 'entries' or 'data' attribute"
        )
    return sum(value for value in values if value is not None) / 1000


async def get_data_tapo(**kwargs) -> MeasurementLogResult:
    client = ApiClient(kwargs["tapo_user"], kwargs["tapo_password"])
    device = await client.p115(kwargs["device_ip"])
    current_draw = (await device.get_current_power()).current_power
    energy_data = await device.get_energy_data(
        EnergyDataInterval.Monthly, datetime(datetime.today().year, 1, 1),
    )
    resolution_kwh = float(kwargs.get("cumulative_energy_resolution_kwh", 0.001))
    return MeasurementLogResult(
        time(),
        current_draw,
        _energy_kwh(energy_data),
        misc={"cumulative_energy_resolution_kwh": resolution_kwh},
    )
