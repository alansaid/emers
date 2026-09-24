"""Shelly Plug Plus S provider."""

import asyncio
from time import time

from requests import post

from emers.measurement import MeasurementLogResult


async def get_data_shelly(**kwargs) -> MeasurementLogResult:
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    request_data = (
        '{"id":1, ' f'"src":"{kwargs["device_id"]}", '
        '"method":"Switch.GetStatus", "params":{"id":0}}'
    )
    response = await asyncio.to_thread(
        post, f"http://{kwargs['device_ip']}/rpc", headers=headers,
        data=request_data, timeout=kwargs.get("request_timeout", 10),
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"API call failed. Status code: {response.status_code}\nResponse: {response}"
        )
    electricity_info = response.json()["result"]
    return MeasurementLogResult(
        time(), electricity_info["apower"],
        electricity_info["aenergy"]["total"] / 1000,
    )
