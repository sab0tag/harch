"""Generate clearly labelled synthetic Mission Control demonstration."""
from pathlib import Path
from .controller import Controller
from .mission_control import export_html


def main():
    controller = Controller("DEMO-001")
    for stage in ("discovery", "triage", "plan", "patch", "tests"):
        controller.advance(evidence="demo:" + stage, passed=True)
    path = export_html(controller.snapshot(), Path("mission-control-demo.html"), mode="demo")
    print(path)


if __name__ == "__main__":
    main()
