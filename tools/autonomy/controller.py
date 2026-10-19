"""Deterministic, side-effect-free engineering workflow controller (dry-run only)."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class State(str, Enum):
    DISCOVER = "DISCOVER"
    TRIAGE = "TRIAGE"
    PLAN = "PLAN"
    EXECUTE = "EXECUTE"
    VERIFY = "VERIFY"
    REVIEW = "REVIEW"
    INTEGRATE = "INTEGRATE"
    DONE = "DONE"
    BLOCKED = "BLOCKED"


NEXT = {
    State.DISCOVER: State.TRIAGE,
    State.TRIAGE: State.PLAN,
    State.PLAN: State.EXECUTE,
    State.EXECUTE: State.VERIFY,
    State.VERIFY: State.REVIEW,
    State.REVIEW: State.INTEGRATE,
    State.INTEGRATE: State.DONE,
}

AGENTS = {
    State.DISCOVER: "Researcher",
    State.TRIAGE: "Researcher",
    State.PLAN: "Architect",
    State.EXECUTE: "Builder",
    State.VERIFY: "Validator",
    State.REVIEW: "Reviewer",
    State.INTEGRATE: "Controller",
}


@dataclass
class Controller:
    task_id: str
    state: State = State.DISCOVER
    events: list[dict[str, Any]] = field(default_factory=list)

    def advance(self, *, evidence: str, passed: bool, approved: bool = False) -> State:
        """Record a verified gate decision. Integration requires separate human approval.

        No code execution, GitHub writes, production access or business workflow operations.
        """
        if self.state in (State.DONE, State.BLOCKED):
            raise ValueError("Terminal state")
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError("Evidence reference required")
        old = self.state
        if not passed:
            new = State.BLOCKED
        elif old == State.INTEGRATE and not approved:
            new = State.BLOCKED
        else:
            new = NEXT[old]
        self.events.append({
            "task_id": self.task_id, "from": old.value, "to": new.value,
            "agent": AGENTS[old], "evidence": evidence, "passed": passed,
            "human_approved": approved if old == State.INTEGRATE else None,
        })
        self.state = new
        return new

    def snapshot(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "state": self.state.value, "events": list(self.events)}
