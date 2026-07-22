"""Unit tests for pure dependency-graph analysis (QM-AUTO-002)."""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

# `pyproject.toml`'s `pythonpath` covers `src` only; `automation/` is a repo-root
# package deliberately outside `src/qualitymesh`, and no conftest.py is in scope to
# add it another way, so the test module adds it itself (matches test_task_loader.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.dependency_resolver import (
    find_dependency_cycles,
    find_incomplete_dependencies,
    find_missing_dependencies,
    find_missing_dependency_ids,
)
from automation.schemas.planning import DependencyCycle, MissingDependency
from automation.schemas.task import AutonomousTask, TaskBacklog, TaskStatus

VERSION = "qualitymesh.dev/automation/v1"

# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------


def build_task(**overrides: Any) -> AutonomousTask:
    task: dict[str, Any] = {
        "id": "task-a",
        "title": "Task A",
        "status": "ready",
        "priority": 1,
        "risk": "low",
        "objective": "Do something.",
        "allowed_paths": ["automation/"],
        "acceptance_criteria": ["It works"],
        "validation_commands": ["uv run pytest tests/automation -q"],
    }
    task.update(overrides)
    return AutonomousTask.model_validate(task)


def build_backlog(*tasks: AutonomousTask) -> TaskBacklog:
    return TaskBacklog.model_validate({"version": VERSION, "tasks": list(tasks)})


def build_self_cycle_task(task_id: str) -> AutonomousTask:
    """A task that depends on itself.

    `AutonomousTask`'s own validator (QM-AUTO-001) rejects this at construction —
    `model_construct` bypasses validation deliberately, so the resolver's own cycle
    detection can be verified independently of that schema guarantee, the same way
    this repository never assumes one boundary's checks are the only safety net
    (see `parsers/junit.py`'s re-verified suite counts).
    """
    return AutonomousTask.model_construct(
        id=task_id,
        title="Self-referential task",
        status=TaskStatus.READY,
        priority=1,
        risk="low",
        objective="Depend on itself.",
        depends_on=(task_id,),
        context_files=(),
        allowed_paths=("automation/",),
        forbidden_paths=(),
        acceptance_criteria=("It works",),
        validation_commands=("uv run pytest tests/automation -q",),
    )


# --------------------------------------------------------------------------------------
# No findings on well-formed graphs
# --------------------------------------------------------------------------------------


def test_backlog_with_no_dependencies_has_no_findings() -> None:
    backlog = build_backlog(build_task(id="a"), build_task(id="b"))

    assert find_missing_dependencies(backlog) == ()
    assert find_dependency_cycles(backlog) == ()


def test_valid_single_dependency_has_no_findings() -> None:
    backlog = build_backlog(
        build_task(id="a", depends_on=["b"]),
        build_task(id="b"),
    )

    assert find_missing_dependencies(backlog) == ()
    assert find_dependency_cycles(backlog) == ()


def test_valid_multi_level_dependency_chain_has_no_findings() -> None:
    backlog = build_backlog(
        build_task(id="a", depends_on=["b"]),
        build_task(id="b", depends_on=["c"]),
        build_task(id="c"),
    )

    assert find_missing_dependencies(backlog) == ()
    assert find_dependency_cycles(backlog) == ()


# --------------------------------------------------------------------------------------
# Missing dependencies
# --------------------------------------------------------------------------------------


def test_missing_dependency_id_is_reported() -> None:
    backlog = build_backlog(build_task(id="a", depends_on=["ghost"]))

    assert find_missing_dependencies(backlog) == (
        MissingDependency(task_id="a", missing_dependency_id="ghost"),
    )
    assert find_dependency_cycles(backlog) == ()


def test_missing_dependency_ids_for_task_excludes_real_references() -> None:
    backlog = build_backlog(
        build_task(id="a", depends_on=["b", "ghost"]),
        build_task(id="b"),
    )

    task_a = backlog.tasks[0]
    assert find_missing_dependency_ids(task_a, backlog) == ("ghost",)


# --------------------------------------------------------------------------------------
# Cycles
# --------------------------------------------------------------------------------------


def test_direct_self_cycle_is_detected() -> None:
    # `TaskBacklog.model_validate` re-checks nested `AutonomousTask` instances (this
    # repo's strict config does not skip revalidation), so the backlog itself is
    # built with `model_construct` too — same bypass, one level up.
    backlog = TaskBacklog.model_construct(version=VERSION, tasks=(build_self_cycle_task("a"),))

    assert find_dependency_cycles(backlog) == (DependencyCycle(task_ids=("a",)),)


def test_two_task_cycle_is_detected() -> None:
    backlog = build_backlog(
        build_task(id="a", depends_on=["b"]),
        build_task(id="b", depends_on=["a"]),
    )

    assert find_dependency_cycles(backlog) == (DependencyCycle(task_ids=("a", "b")),)


def test_three_or_more_task_indirect_cycle_is_detected() -> None:
    backlog = build_backlog(
        build_task(id="a", depends_on=["b"]),
        build_task(id="b", depends_on=["c"]),
        build_task(id="c", depends_on=["a"]),
    )

    assert find_dependency_cycles(backlog) == (DependencyCycle(task_ids=("a", "b", "c")),)


def test_cycle_reporting_is_deterministic() -> None:
    backlog = build_backlog(
        build_task(id="a", depends_on=["b"]),
        build_task(id="b", depends_on=["c"]),
        build_task(id="c", depends_on=["a"]),
    )

    first = find_dependency_cycles(backlog)
    second = find_dependency_cycles(backlog)
    assert first == second


def test_dependency_graph_independent_of_task_declaration_order() -> None:
    forward = build_backlog(
        build_task(id="a", depends_on=["b"]),
        build_task(id="b", depends_on=["c"]),
        build_task(id="c", depends_on=["a"]),
        build_task(id="d", depends_on=["ghost"]),
    )
    reversed_order = build_backlog(
        build_task(id="d", depends_on=["ghost"]),
        build_task(id="c", depends_on=["a"]),
        build_task(id="b", depends_on=["c"]),
        build_task(id="a", depends_on=["b"]),
    )

    assert find_dependency_cycles(forward) == find_dependency_cycles(reversed_order)
    assert find_missing_dependencies(forward) == find_missing_dependencies(reversed_order)


def test_cycle_not_reported_for_unrelated_tasks() -> None:
    backlog = build_backlog(
        build_task(id="a", depends_on=["b"]),
        build_task(id="b", depends_on=["a"]),
        build_task(id="standalone"),
    )

    cycles = find_dependency_cycles(backlog)
    assert cycles == (DependencyCycle(task_ids=("a", "b")),)
    assert all("standalone" not in cycle.task_ids for cycle in cycles)


# --------------------------------------------------------------------------------------
# Incomplete dependencies
# --------------------------------------------------------------------------------------


def test_incomplete_dependency_is_reported() -> None:
    backlog = build_backlog(
        build_task(id="a", depends_on=["b"]),
        build_task(id="b", status="active"),
    )

    task_a = backlog.tasks[0]
    assert find_incomplete_dependencies(task_a, backlog) == ("b",)


def test_completed_dependency_is_not_reported_incomplete() -> None:
    backlog = build_backlog(
        build_task(id="a", depends_on=["b"]),
        build_task(id="b", status="completed"),
    )

    task_a = backlog.tasks[0]
    assert find_incomplete_dependencies(task_a, backlog) == ()


def test_missing_dependency_is_not_double_reported_as_incomplete() -> None:
    backlog = build_backlog(build_task(id="a", depends_on=["ghost"]))

    task_a = backlog.tasks[0]
    assert find_incomplete_dependencies(task_a, backlog) == ()
    assert find_missing_dependency_ids(task_a, backlog) == ("ghost",)


# --------------------------------------------------------------------------------------
# No mutation
# --------------------------------------------------------------------------------------


def test_resolver_functions_do_not_mutate_backlog() -> None:
    backlog = build_backlog(
        build_task(id="a", depends_on=["b"]),
        build_task(id="b", depends_on=["a"]),
        build_task(id="c", depends_on=["ghost"]),
    )
    before = copy.deepcopy(backlog)

    find_missing_dependencies(backlog)
    find_dependency_cycles(backlog)
    for task in backlog.tasks:
        find_incomplete_dependencies(task, backlog)
        find_missing_dependency_ids(task, backlog)

    assert backlog == before
