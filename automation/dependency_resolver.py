"""Pure, deterministic dependency-graph analysis over a task backlog (QM-AUTO-002).

Answers structural questions about `TaskBacklog.tasks[*].depends_on` only: which
references name no task in the backlog, which tasks form a cycle, and which of a
task's existing dependencies aren't `completed` yet. Nothing here mutates a backlog,
runs a command, or decides whether a task should be picked — `automation.task_selector`
combines these results with a task's own status and risk to make that call.
"""

from __future__ import annotations

from automation.schemas.planning import DependencyCycle, MissingDependency
from automation.schemas.task import AutonomousTask, TaskBacklog, TaskStatus


def find_missing_dependencies(backlog: TaskBacklog) -> tuple[MissingDependency, ...]:
    """Every `depends_on` reference across `backlog` that names no task in it.

    Sorted by `(task_id, missing_dependency_id)`, so the result is independent of
    the order tasks were declared in `backlog.tasks`.
    """
    known_ids = {task.id for task in backlog.tasks}
    missing = [
        MissingDependency(task_id=task.id, missing_dependency_id=dependency_id)
        for task in backlog.tasks
        for dependency_id in task.depends_on
        if dependency_id not in known_ids
    ]
    return tuple(sorted(missing, key=lambda item: (item.task_id, item.missing_dependency_id)))


def find_missing_dependency_ids(task: AutonomousTask, backlog: TaskBacklog) -> tuple[str, ...]:
    """The subset of `task.depends_on` that names no task in `backlog`, sorted."""
    known_ids = {other.id for other in backlog.tasks}
    return tuple(sorted(dep_id for dep_id in task.depends_on if dep_id not in known_ids))


def find_incomplete_dependencies(task: AutonomousTask, backlog: TaskBacklog) -> tuple[str, ...]:
    """The subset of `task.depends_on` that names a real task not yet `completed`.

    Excludes references that name no task at all — those are
    `find_missing_dependency_ids`'s concern, so the two results never overlap.
    """
    by_id = {other.id: other for other in backlog.tasks}
    incomplete = (
        dep_id
        for dep_id in task.depends_on
        if dep_id in by_id and by_id[dep_id].status is not TaskStatus.COMPLETED
    )
    return tuple(sorted(incomplete))


def find_dependency_cycles(backlog: TaskBacklog) -> tuple[DependencyCycle, ...]:
    """Every group of tasks in `backlog` that depend on each other, directly or
    indirectly — a strongly connected component of size greater than one, or a
    single task that (invalidly) depends on itself.

    Uses Tarjan's algorithm, restricted to edges that reference a real task: a
    missing reference is `find_missing_dependencies`'s concern, not a graph edge
    here. Strongly-connected-component membership is a structural property of the
    graph and does not depend on traversal order, but the order components are
    *discovered* in does — so the returned tuple is explicitly sorted, making the
    result independent of `backlog.tasks` declaration order.
    """
    known_ids = {task.id for task in backlog.tasks}
    adjacency = {
        task.id: tuple(dep_id for dep_id in task.depends_on if dep_id in known_ids)
        for task in backlog.tasks
    }

    index_counter = 0
    index: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    stack: list[str] = []
    components: list[list[str]] = []

    def strongconnect(node: str) -> None:
        nonlocal index_counter
        index[node] = index_counter
        lowlink[node] = index_counter
        index_counter += 1
        stack.append(node)
        on_stack[node] = True

        for neighbor in adjacency[node]:
            if neighbor not in index:
                strongconnect(neighbor)
                lowlink[node] = min(lowlink[node], lowlink[neighbor])
            elif on_stack.get(neighbor, False):
                lowlink[node] = min(lowlink[node], index[neighbor])

        if lowlink[node] == index[node]:
            component: list[str] = []
            while True:
                member = stack.pop()
                on_stack[member] = False
                component.append(member)
                if member == node:
                    break
            components.append(component)

    for task_id in sorted(adjacency):
        if task_id not in index:
            strongconnect(task_id)

    cycles = [
        DependencyCycle(task_ids=tuple(sorted(component)))
        for component in components
        if len(component) > 1 or component[0] in adjacency[component[0]]
    ]
    return tuple(sorted(cycles, key=lambda cycle: cycle.task_ids))
