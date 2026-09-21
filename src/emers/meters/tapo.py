from time import time
from datetime import datetime
from tapo import ApiClient
from tapo.requests import EnergyDataInterval

from emers.measurement import MeasurementLogResult


def _energy_kwh(result):
    """Return total energy in kWh across old and current tapo responses."""
    if hasattr(result, "entries"):
        # tapo >= 0.8 returns EnergyDataIntervalResult objects. Each energy
        # value is optional and expressed in Wh.
        values = (entry.energy for entry in result.entries)
    elif hasattr(result, "data"):
        # tapo 0.3 exposed the Wh readings directly as ``data``.
        values = result.data
    else:
        raise TypeError(
            "Unsupported tapo energy response: expected an 'entries' or 'data' attribute"
        )

    return sum(value for value in values if value is not None) / 1000


async def get_data_tapo(**kwargs) -> MeasurementLogResult:
    """
    Get data from TP-Link Tapo P115
    :param kwargs: Must include "device_ip", "tapo_user", and "tapo_password"
    :return: PowerLogResult containing energy readings
    """
    client = ApiClient(kwargs["tapo_user"], kwargs["tapo_password"])
    device = await client.p115(kwargs["device_ip"])

    current_draw = await device.get_current_power()
    current_draw = current_draw.current_power
    energy_data = await device.get_energy_data(
        EnergyDataInterval.Monthly,
        datetime(datetime.today().year, 1, 1),
    )
    total_draw = _energy_kwh(energy_data)
    timestamp = time()

    return MeasurementLogResult(timestamp=timestamp, current_draw=current_draw, total_draw=total_draw, misc=None)
