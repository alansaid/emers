# EMERS: Energy Meter for Recommender Systems

EMERS measures, records, visualizes, and reports the energy consumption of
recommender-system experiments using supported smart plugs.

The accompanying paper was accepted at the
[RecSoGood 2024 Workshop](https://recsogood.github.io/recsogood24/), colocated
with the 18th ACM Conference on Recommender Systems. A
[demo video](https://youtu.be/vmXOcrVpRDg) shows the original workflow.

## Installation

EMERS requires Python 3.10 or newer.

```bash
python -m pip install emers
```

For development from a clone:

```bash
python -m pip install -e .
```

## Quick start

Initialize a workspace in the current directory:

```bash
emers init
```

This safely creates `settings.json`, `monitor_settings.json`, `measurements/`,
and `report/`. Existing configuration files are never overwritten. The default
configuration includes a mock smart plug, so EMERS can be tried immediately:

```bash
emers measure
```

EMERS lists the devices in `settings.json`. Choose one to begin measuring or
select **Configure a new device** to add a Mock, Shelly, or Tapo plug. Tapo
password input is hidden. The same menu can edit or remove a configured device;
removal requires confirmation and historical measurements are retained. When
editing a Tapo plug, leave the password blank to keep the existing one. To skip
the interactive prompt, provide the saved device name directly. Renaming a
device does not rename its historical measurement directory.

```bash
emers measure --device MockPlug
```

Press Ctrl+C to stop recording, then launch the monitoring interface:

```bash
emers monitor
```

Open <http://127.0.0.1:5000> in a browser.

For the usual local workflow, run measurement and the dashboard together:

```bash
emers run
```

The selected plug is measured in the background while the web UI runs at
<http://127.0.0.1:5000>. Press Ctrl+C to stop both. The separate `measure` and
`monitor` commands remain useful when they have different lifecycles or share a
workspace between processes.

Use `--workspace` to keep data in another directory:

```bash
emers --workspace path/to/experiment init
emers --workspace path/to/experiment measure --device MockPlug
emers --workspace path/to/experiment monitor
```

Run `emers --help` or `emers <command> --help` for all options. EMERS can also
be invoked as `python -m emers`.

## Configuring a smart plug

Devices are defined in the workspace's `settings.json`. Do not commit this file
when it contains credentials. See [`settings.example.json`](settings.example.json)
for a safe example.

### Shelly Plug Plus S

```json
{
  "shelly_meter": {
    "device_type": "shelly",
    "device_ip": "192.168.2.1",
    "device_id": "0"
  }
}
```

### TP-Link Tapo P115

```json
{
  "tapo_meter": {
    "device_type": "tapo",
    "device_ip": "192.168.2.2",
    "tapo_user": "yourname@example.com",
    "tapo_password": "password"
  }
}
```

The plug and the computer running EMERS must be reachable on the same network.

## Measuring from Python

`MeasurementManager` is a context manager that records measurements while an
experiment runs:

```python
from emers import MeasurementManager

with MeasurementManager(
    device_name="shelly_meter",
    experiment_name="model-training",
):
    model.fit(train_data)
```

Its optional `workspace` argument selects the directory containing
`settings.json` and receiving measurements. The default is the current working
directory.

## Adding a meter integration

Add a module under `src/emers/meters/` named after the device type. For a
device type named `example`, implement:

```python
async def get_data_example(**kwargs) -> MeasurementLogResult:
    ...
```

The function must return an `emers.MeasurementLogResult`. Document all required
configuration keys and add tests for the integration.

## Development and releases

Install the project in editable mode with test tools, then run:

```bash
pytest
python -m build
python -m twine check dist/*
```

Release artifacts should include both the wheel and source distribution from
`dist/`. Publishing through PyPI Trusted Publishing is recommended.

## License

EMERS is released under the [MIT License](LICENSE).
