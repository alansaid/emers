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

Install the optional CodeCarbon provider when software estimates or automatic
fallback are required:

```bash
python -m pip install "emers[codecarbon]"
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
select **Configure a new device** to add a Mock, Shelly, Tapo, or CodeCarbon
provider. Tapo
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

### CodeCarbon software estimates

Choose the CodeCarbon device type when no physical meter is available and an
estimate is preferable to the synthetic mock data. EMERS records these values
as estimates of CPU, GPU, and RAM energy, so they are not presented as
equivalent to whole-system wall-power readings. Supplying a three-letter
country code such as `SWE` enables CodeCarbon's offline tracker and avoids a
location lookup.

## Measuring from Python

The recommended Python API creates a structured run around the experiment:

```python
import emers

with emers.run(
    "model-training",
    device="shelly_meter",
    parameters={"model": "BPR", "factors": 128},
    tags=["baseline"],
) as run:
    with run.phase("training"):
        model.fit(train_data)

    with run.phase("evaluation"):
        metrics = evaluate(model, test_data)
        run.log_metrics(metrics)
```

Runs can also be applied as a decorator:

```python
from emers import track

@track("model-training", device="shelly_meter")
def train():
    ...
```

The optional `workspace` argument selects the directory containing
`settings.json` and receiving measurements. `MeasurementManager` remains
available as the lower-level acquisition API.

### Meter outages and fallback

The default failure policy is `fallback`:

1. EMERS probes the configured meter before entering the experiment block.
2. If the plug is unavailable, EMERS tries CodeCarbon immediately.
3. During a run, transient failures are retried with bounded exponential
   backoff; after three consecutive failures EMERS switches to CodeCarbon.
4. If CodeCarbon is unavailable, the experiment continues while EMERS records
   explicit measurement gaps and keeps retrying the plug.

Fallback is never silent. CSV rows include `source`, `measurement_method`, and
`measurement_scope`; CodeCarbon rows are labelled `estimated` and
`cpu_gpu_ram`, while smart-plug readings are labelled `measured` and
`whole_system_wall`.

Choose another policy in Python or on the CLI:

```python
with emers.run("strict-run", device="tapo_meter", failure_policy="fail"):
    ...
```

```bash
emers run --device tapo_meter --failure-policy continue
emers run --device tapo_meter --failure-policy fail
```

- `continue` records gaps and retries the original provider without switching.
- `fail` rejects an unavailable provider before the experiment starts. A
  persistent failure during the experiment is raised when the context exits,
  because Python cannot safely inject an exception into user code from the
  measurement thread.

### Run artifacts

Every run receives a UUID and writes a manifest and append-only event log under:

```text
measurements/<device>/<experiment>/.emers/runs/<run-id>/
├── run.json
└── events.jsonl
```

The manifest records runtime and Git metadata, parameters, latest metrics,
phases, measurement provenance, provider changes, errors, and final status.
Device credentials are never copied into run artifacts. Measurement CSVs remain
in the experiment directory and include the run UUID and provenance fields.

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
