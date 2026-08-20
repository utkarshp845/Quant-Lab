"""Pure logic for the experiment-versioning view (spec section 10:
"Experiment 2 -> Definition C -> Locked; Experiment 2A -> Definition C +
changed threshold; Experiment 2B -> Definition B. Show what changed
between versions.") -- no I/O, matching this app's "engine" discipline.
app/api/research_notebook.py is the only caller, and does the fetching.

A "version tree" here is nothing more than plain Experiment rows linked
by `parent_experiment_id` (app/models/research.py) -- there is no
separate "version" entity to duplicate Experiment's own fields into.
"""

import json

from app.models.research import Experiment
from app.models.research_notebook import ExperimentFieldDiff
from app.research.lifecycle import canonicalize_hypothesis


def find_root(target_id: str, by_id: dict[str, Experiment]) -> str:
    """Walks `parent_experiment_id` up from `target_id` until it hits an
    experiment with no parent (or a parent this dataset doesn't have,
    or a cycle -- neither of which this app's own creation path can
    produce, but this is defensive, not assumed). Returns that root
    experiment's own id."""
    current = by_id.get(target_id)
    if current is None:
        raise ValueError(f"No experiment with id {target_id!r}")
    seen = {current.id}
    while current.parent_experiment_id and current.parent_experiment_id in by_id:
        parent = by_id[current.parent_experiment_id]
        if parent.id in seen:
            break  # a cycle should never exist, but never loop forever if one somehow does
        current = parent
        seen.add(current.id)
    return current.id


def collect_version_tree(root_id: str, all_experiments: list[Experiment]) -> list[Experiment]:
    """Every experiment reachable from `root_id` by following
    `parent_experiment_id` links downward (inclusive of the root
    itself), breadth-first -- the full set of "candidates and versions"
    spec section 10 asks to be inspectable together."""
    children_by_parent: dict[str, list[Experiment]] = {}
    by_id: dict[str, Experiment] = {}
    for experiment in all_experiments:
        by_id[experiment.id] = experiment
        if experiment.parent_experiment_id:
            children_by_parent.setdefault(experiment.parent_experiment_id, []).append(experiment)

    if root_id not in by_id:
        return []

    versions: list[Experiment] = []
    visited: set[str] = set()
    queue = [root_id]
    while queue:
        node_id = queue.pop(0)
        if node_id in visited:
            continue
        visited.add(node_id)
        versions.append(by_id[node_id])
        queue.extend(child.id for child in children_by_parent.get(node_id, []))
    return versions


def diff_experiments(parent: Experiment, child: Experiment) -> list[ExperimentFieldDiff]:
    """Field-level diff over exactly the research-defining fields
    app/research/lifecycle.py::canonicalize_hypothesis() already
    enumerates for hypothesis-hash purposes (reused unmodified -- the
    field set that defines what an experiment MEANS is the field set
    worth showing a reader "what changed between versions")."""
    parent_fields = canonicalize_hypothesis(parent)
    child_fields = canonicalize_hypothesis(child)
    diffs: list[ExperimentFieldDiff] = []
    for field in sorted(set(parent_fields) | set(child_fields)):
        parent_value = parent_fields.get(field)
        child_value = child_fields.get(field)
        if parent_value != child_value:
            diffs.append(
                ExperimentFieldDiff(
                    field=field,
                    parent_value=json.dumps(parent_value, sort_keys=True),
                    child_value=json.dumps(child_value, sort_keys=True),
                )
            )
    return diffs
