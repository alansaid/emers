import json

from emers.cli import init_workspace
from emers.session import Run


def test_run_records_metadata_parameters_metrics_and_phases(tmp_path):
    init_workspace(tmp_path)

    with Run(
        "training",
        device="MockPlug",
        workspace=tmp_path,
        polling_rate=0.001,
        parameters={"model": "BPR"},
        tags=["smoke-test"],
    ) as run:
        run.log_params({"epochs": 5})
        with run.phase("fit", split="train"):
            run.log_metric("loss", 0.25, step=1)
        manifest_path = run.manifest_path

    manifest = json.loads(manifest_path.read_text())
    assert manifest["status"] == "completed"
    assert manifest["parameters"] == {"epochs": 5, "model": "BPR"}
    assert manifest["metrics"] == {"loss": 0.25}
    assert manifest["tags"] == ["smoke-test"]
    assert manifest["phases"][0]["name"] == "fit"
    assert manifest["phases"][0]["status"] == "completed"
    assert manifest["measurement"]["method"] == "synthetic"
