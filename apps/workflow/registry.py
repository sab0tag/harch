"""Registry of task types (docs/03_task_engine.md). Views never hardcode chains — they ask here."""
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable

_REGISTRY: dict[str, "TaskDefinition"] = {}


def _no_errors(task) -> list[str]:
    return []


def _nothing(task) -> None:
    return None


@dataclass(frozen=True)
class TaskDefinition:
    type: str
    title: str
    maker_role: str
    checker_role: str
    deadline: Callable[[date], datetime | None]
    per_department: bool = False
    description: str = ""
    validate_submit: Callable = field(default=_no_errors)   # (task) -> list of error messages
    on_approved: Callable = field(default=_nothing)          # (task) -> None, runs inside the approve transaction
    on_cancelled: Callable = field(default=_nothing)         # (task) -> None, runs inside the cancel transaction
    subject_url: Callable | None = None                      # (task) -> url of the object to work on
    action_label: str = "Відкрити документ"                  # call to action for the maker on that object
    # (task) -> queryset of tasks that on_approved reads under lock. Locked together, in pk
    # order, before the task itself — otherwise two parallel approvals deadlock.
    lock_group: Callable | None = None
    # The work only makes sense before its service date has passed (a department order). Once the day is
    # over, only an admin may still start/submit it (catching up a missed day), and the nightly
    # expire_stale_tasks closes what was left open.
    expires_with_day: bool = False


def register(definition: TaskDefinition) -> TaskDefinition:
    _REGISTRY[definition.type] = definition
    return definition


def unregister(type_: str) -> None:
    _REGISTRY.pop(type_, None)


def get(type_: str) -> TaskDefinition:
    try:
        return _REGISTRY[type_]
    except KeyError as exc:
        raise KeyError(f"Unknown task type: {type_}") from exc


def all_definitions() -> list[TaskDefinition]:
    return list(_REGISTRY.values())


def choices() -> list[tuple[str, str]]:
    return [(d.type, d.title) for d in _REGISTRY.values()]
