"""Bounded read-only parallel checks for core project invariants."""
import concurrent.futures
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKS = {
    "consumption": ["apps/consumption/calc.py", "apps/consumption/models.py"],
    "authorization": ["apps/accounts/models.py", "apps/workflow/services.py"],
    "workflow": ["apps/workflow/services.py", "apps/workflow/registry.py"],
    "data_integrity": ["apps/planning/models.py", "apps/consumption/models.py"],
}


def inspect(scope, paths):
    missing = [path for path in paths if not (ROOT / path).is_file()]
    return {"scope": scope, "status": "failed" if missing else "complete", "inspected_files": [p for p in paths if p not in missing], "errors": ["Missing: " + p for p in missing], "findings": []}


def run():
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(inspect, scope, paths) for scope, paths in CHECKS.items()]
        workers = [future.result() for future in futures]
    complete = len(workers) == len(CHECKS) and all(w["status"] == "complete" for w in workers)
    return {"complete": complete, "workers": workers, "warning": "File-presence smoke check only; does not verify correctness or execute tests."}


if __name__ == "__main__":
    report = run()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["complete"] else 1)
