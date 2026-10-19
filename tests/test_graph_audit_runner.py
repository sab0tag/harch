"""Graph audit runner smoke tests."""
import importlib.util
from pathlib import Path


def load_runner():
    path = Path(__file__).resolve().parents[1] / "tools" / "graph_audit.py"
    spec = importlib.util.spec_from_file_location("graph_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_graph_worker_fails_on_missing_file():
    module = load_runner()
    result = module.inspect("workflow", ["not_a_real_file_123.py"])
    assert result["status"] == "failed"
    assert result["errors"]


def test_graph_runner_returns_all_scopes():
    module = load_runner()
    report = module.run()
    assert {w["scope"] for w in report["workers"]} == set(module.CHECKS)
    assert len(report["workers"]) == 4
