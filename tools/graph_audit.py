"""Bounded read-only parallel checks for core project invariants."""
import argparse
import concurrent.futures
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKERS = {
    "apps/consumption/calc.py": ("Decimal", "brutto_pp", "netto_pp"),
    "apps/consumption/models.py": ("ConsumptionSnapshotLine", "brutto_total_g"),
    "apps/accounts/models.py": ("department", "Role"),
    "apps/workflow/services.py": ("is_checker", "submitted_by_id", "TaskEvent"),
    "apps/workflow/registry.py": ("TaskDefinition", "register"),
    "apps/planning/models.py": ("plan_entry_unique_diet", "self.menu.diet_id"),
}

CHECKS = {
    "consumption": ["apps/consumption/calc.py", "apps/consumption/models.py"],
    "authorization": ["apps/accounts/models.py", "apps/workflow/services.py"],
    "workflow": ["apps/workflow/services.py", "apps/workflow/registry.py"],
    "data_integrity": ["apps/planning/models.py", "apps/consumption/models.py"],
}


def inspect(scope, paths):
    missing = [path for path in paths if not (ROOT / path).is_file()]
    findings = []
    for path in paths:
        if path in missing:
            continue
        content = (ROOT / path).read_text(encoding="utf-8")
        for marker in MARKERS.get(path, ()):
            if marker not in content:
                findings.append({"path": path, "marker": marker, "status": "needs_human_review"})
    return {"scope": scope, "status": "failed" if missing else "complete",
            "inspected_files": [p for p in paths if p not in missing],
            "errors": ["Missing: " + p for p in missing], "findings": findings}


def run():
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(inspect, scope, paths) for scope, paths in CHECKS.items()]
        workers = [future.result() for future in futures]
    complete = len(workers) == len(CHECKS) and all(w["status"] == "complete" and not w["findings"] for w in workers)
    return {"complete": complete, "workers": workers, "warning": "Static invariant-marker smoke check only; does not prove correctness or execute application tests."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", help="Write machine-readable report to this file")
    args = parser.parse_args()
    report = run()
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if report["complete"] else 1)
