import asyncio
import csv
import importlib
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from time import time
from typing import Optional

@dataclass
class MeasurementLogResult:
    """
    Dataclass to store the result of a smart plug reading.
    """
    timestamp: float
    current_draw: float
    total_draw: float
    misc: Optional[dict] = None


class MeasurementManager:
    """
    Class to manage the measuring and logging of power data from a smart plug.
    """

    def __init__(self, device_name, experiment_name=None, polling_rate=0.5, log_interval=300,
                 workspace=None, config=None):
        """
        Initialize the MeasurementManager.
        :param device_name: The name of the device that will be used to retrieve connection parameters.
        :param experiment_name: The name of the experiment that this data will be logged under.
        :param polling_rate: The rate at which the device will be polled for data in seconds.
        :param log_interval: The interval at which the log file will be rotated in seconds.
        :param workspace: Directory containing settings.json and receiving output.
        :param config: Optional path to a settings JSON file. Relative paths are
            resolved from the workspace.
        """
        self.stop_event = threading.Event()
        self.loop_thread = None
        self.device_name = device_name
        self.experiment_name = experiment_name
        self.polling_rate = polling_rate
        self.log_interval = log_interval
        self.workspace = Path(workspace or ".").expanduser().resolve()
        config_path = Path(config or "settings.json").expanduser()
        if not config_path.is_absolute():
            config_path = self.workspace / config_path

        try:
            file = config_path.open("r", encoding="utf-8")
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"Configuration not found at {config_path}. Run 'emers init' first."
            ) from exc

        with file:
            devices = json.load(file)

        if self.device_name not in devices:
            raise ValueError(f"Device {self.device_name} not found in settings.json")

        self.device = devices[self.device_name]

    def _start_experiment_logging(self):
        """
        Start the experiment logging. Private method.
        """
        if self.loop_thread is None or not self.loop_thread.is_alive():
            self.stop_event.clear()

            def async_intermediate():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self.log_data())
                loop.close()

            self.loop_thread = threading.Thread(target=async_intermediate)
            self.loop_thread.start()

        print(f"EMERS logging started for device {self.device_name}, "
              f"experiment {self.experiment_name} with "
              f"polling rate {self.polling_rate} and "
              f"log interval {self.log_interval}.")

    def _finish_experiment_logging(self):
        """
        Finish the experiment logging. Private method.
        """
        if self.loop_thread is not None and self.loop_thread.is_alive():
            self.stop_event.set()
            self.loop_thread.join()
            self.loop_thread = None

        print(f"EMERS logging stopped for device {self.device_name}, "
              f"experiment {self.experiment_name} with "
              f"polling rate {self.polling_rate} and "
              f"log interval {self.log_interval}.")

    def __enter__(self):
        """
        Enter the context manager.
        """
        self._start_experiment_logging()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Exit the context manager.
        """
        self._finish_experiment_logging()

    async def log_data(self):
        """
        Log data from the smart plug.
        """
        device_type = self.device["device_type"]

        module_name = f"emers.meters.{device_type}"
        function_name = f"get_data_{device_type}"

        try:
            module = importlib.import_module(module_name)
            api = getattr(module, function_name)
        except (ImportError, AttributeError) as exc:
            raise ImportError(f"Could not load meter implementation {module_name}: {exc}") from exc

        if self.experiment_name is not None:
            log_base = self.workspace / "measurements" / self.device_name / self.experiment_name
            log_base.mkdir(exist_ok=True, parents=True)
        else:
            log_base = self.workspace / "measurements" / self.device_name
            log_base.mkdir(exist_ok=True, parents=True)

        start_timestamp = time()

        log_file_name = log_base / f"{start_timestamp}.csv"

        while not self.stop_event.is_set():
            if not Path(log_file_name).exists() or start_timestamp + self.log_interval <= time():
                if start_timestamp + self.log_interval <= time():
                    start_timestamp += self.log_interval
                log_file_name = log_base / f"{start_timestamp}.csv"
                with open(log_file_name, 'w', newline='') as log_file:
                    writer = csv.writer(log_file)
                    writer.writerow(['timestamp', 'current_draw', 'total_draw'])

            result: MeasurementLogResult = await api(**self.device)

            with open(log_file_name, 'a', newline='') as log_file:
                writer = csv.writer(log_file)
                writer.writerow([result.timestamp, result.current_draw, result.total_draw])

            await asyncio.sleep(self.polling_rate)
