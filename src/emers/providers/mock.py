"""Synthetic provider for demonstrations and tests."""

from random import randint
from time import time

from emers.measurement import MeasurementLogResult


async def get_data_mock(**kwargs) -> MeasurementLogResult:
    return MeasurementLogResult(time(), randint(20, 250), randint(1, 5))
