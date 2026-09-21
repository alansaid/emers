from random import randint
from time import time

from emers.measurement import MeasurementLogResult


async def get_data_mock(**kwargs) -> MeasurementLogResult:
    """
    Get data from mock device for debugging purposes
    :param kwargs: No keyword arguments required
    :return: PowerLogResult containing energy readings with randomized values
    """
    current_draw = randint(20, 250)
    total_draw = randint(1, 5)
    timestamp = time()

    return MeasurementLogResult(timestamp=timestamp, current_draw=current_draw, total_draw=total_draw, misc=None)
