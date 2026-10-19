"""Tests for the bounded read-only graph audit runner."""
import importlib.util
import unittest
from pathlib import Path


def load_runner():
    path = Path(__file__).resolve().parents[1] / "tools" / "graph_audit.py"
    spec = importlib.util.spec_from_file_location("graph_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GraphAuditTests(unittest.TestCase):
    def test_missing_file_fails_closed(self):
        module = load_runner()
        result = module.inspect("workflow", ["not_a_real_file_123.py"])
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["errors"])

    def test_all_scopes_are_reported(self):
        module = load_runner()
        report = module.run()
        self.assertEqual({w["scope"] for w in report["workers"]}, set(module.CHECKS))
        self.assertEqual(len(report["workers"]), 4)

    def test_missing_marker_is_flagged_for_review(self):
        module = load_runner()
        original = module.MARKERS
        try:
            module.MARKERS = {"apps/consumption/calc.py": ("impossible_marker_123",)}
            result = module.inspect("consumption", ["apps/consumption/calc.py"])
            self.assertEqual(result["findings"][0]["status"], "needs_human_review")
        finally:
            module.MARKERS = original


if __name__ == "__main__":
    unittest.main()
