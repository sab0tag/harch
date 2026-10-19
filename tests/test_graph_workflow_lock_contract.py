"""Guard the locking contract that serializes competing workflow approvals.

This is a structural regression check, not a substitute for a PostgreSQL
two-connection concurrency test.
"""
import ast
from pathlib import Path


SERVICES = Path(__file__).resolve().parents[1] / "apps/workflow/services.py"


def _function(tree, name):
    return next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)


def _calls(function):
    return {
        ".".join(reversed(_attr_parts(node.func)))
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
    }


def _attr_parts(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return parts


def test_approve_is_atomic_and_reloads_locked_task():
    tree = ast.parse(SERVICES.read_text(encoding="utf-8"))
    approve = _function(tree, "approve")
    assert "transaction.atomic" in _calls(approve)
    assert "_locked" in _calls(approve)
    assert "_require" in _calls(approve)
    assert "_event" in _calls(approve)


def test_locked_helper_uses_select_for_update():
    tree = ast.parse(SERVICES.read_text(encoding="utf-8"))
    locked = _function(tree, "_locked")
    assert "Task.objects.select_for_update" in _calls(locked)
