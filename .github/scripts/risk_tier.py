"""PR risk tier from the files it changes (see .claude/commands/steward.md, «Ворота»).

low  — only docs, Claude commands, tests, or requirement bound changes of the same packages;
high — anything else (domain code, migrations, templates, settings, deploy, CI).
Decided by paths, never by an agent. Low-risk PRs may be auto-merged once CI is green.

    python .github/scripts/risk_tier.py <base-ref>     # prints "low" or "high"
"""
import re
import subprocess
import sys

LOW_PREFIXES = ("docs/", ".claude/")
LOW_FILES = {"README.md", "KICKOFF.md", "conftest.py"}
TEST_PATH = re.compile(r"^apps/[^/]+/tests/")
REQUIREMENTS = re.compile(r"^requirements(-dev)?\.txt$")


def package_names(text: str) -> set[str]:
    names = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[\[<>=!~; ]", line, maxsplit=1)[0]
        names.add(name.lower().replace("_", "-"))
    return names


def classify(paths, requirement_texts=None) -> str:
    """paths: changed file paths; requirement_texts: {path: (old_text, new_text)} for requirements files."""
    requirement_texts = requirement_texts or {}
    for path in paths:
        if path.startswith(LOW_PREFIXES) or path in LOW_FILES or TEST_PATH.match(path):
            continue
        if REQUIREMENTS.match(path):
            old, new = requirement_texts.get(path, ("", None))
            if new is not None and package_names(old) == package_names(new):
                continue                       # same packages, only bounds moved
        return "high"
    return "low"


def _git(*args) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def main(base: str) -> str:
    paths = [p for p in _git("diff", "--name-only", f"{base}...HEAD").splitlines() if p]
    texts = {}
    for p in paths:
        if REQUIREMENTS.match(p):
            try:
                old = _git("show", f"{base}:{p}")
            except subprocess.CalledProcessError:
                old = ""
            try:
                new = open(p, encoding="utf-8").read()
            except FileNotFoundError:
                new = None                     # deleted requirements file → high
            texts[p] = (old, new)
    return classify(paths, texts)


if __name__ == "__main__":
    print(main(sys.argv[1] if len(sys.argv) > 1 else "origin/main"))
