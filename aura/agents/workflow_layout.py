"""Deterministic native graph layout shared by construction and previews."""

from __future__ import annotations

from dataclasses import replace

from aura.agents.graph_dag import runnable_dag
from aura.agents.graph_models import Point, WorkflowGraph
from aura.agents.helper_topology import read_helper_topology

_SOLID_X_GAP = 320.0
_SOLID_Y_GAP = 180.0
_HELPER_X_GAP = 220.0
_HELPER_Y_GAP = 180.0


def _legacy_layout(graph: WorkflowGraph) -> WorkflowGraph:
    """Give a valid graph a small deterministic rank-and-row layout."""
    dag = runnable_dag(graph)
    if dag is None:
        return graph

    ranks: dict[str, int] = {}
    for step in dag.steps:
        ranks[step.node_id] = 1 + max(
            (ranks[node_id] for node_id in step.predecessors),
            default=0,
        )
    result_rank = 1 + max(
        (ranks[node_id] for node_id in dag.terminal_node_ids),
        default=0,
    )
    center_x = result_rank * _SOLID_X_GAP / 2.0
    positions: dict[str, Point] = {
        dag.task_node_id: Point(-center_x, 0.0),
        dag.result_node_id: Point(result_rank * _SOLID_X_GAP - center_x, 0.0),
    }

    by_rank: dict[int, list[str]] = {}
    for step in dag.steps:
        by_rank.setdefault(ranks[step.node_id], []).append(step.node_id)
    for rank, node_ids in by_rank.items():
        middle = (len(node_ids) - 1) / 2.0
        for index, node_id in enumerate(node_ids):
            positions[node_id] = Point(
                rank * _SOLID_X_GAP - center_x,
                (index - middle) * _SOLID_Y_GAP,
            )

    topology = read_helper_topology(graph)
    solid_bottom = max((point.y for point in positions.values()), default=0.0)
    next_helper_y = solid_bottom + _HELPER_Y_GAP
    for root_node_id in dag.node_ids:
        descendants = topology.preorder_for_root(root_node_id)
        if not descendants:
            continue
        max_depth = max(item.depth for item in descendants)
        base_y = next_helper_y
        pending = [root_node_id]
        while pending:
            parent_id = pending.pop(0)
            children = topology.children_of(parent_id)
            if not children:
                continue
            parent_x = positions[parent_id].x
            middle = (len(children) - 1) / 2.0
            for index, child in enumerate(children):
                positions[child.node_id] = Point(
                    parent_x + (index - middle) * _HELPER_X_GAP,
                    base_y + (child.depth - 1) * _HELPER_Y_GAP,
                )
                pending.append(child.node_id)
        next_helper_y += max_depth * _HELPER_Y_GAP

    return replace(
        graph,
        nodes=tuple(replace(node, position=positions.get(node.node_id, node.position)) for node in graph.nodes),
    )


# Native node bodies are 196 × 68, with a helper port beneath them. Keep
# layout independent of Qt while leaving room for ports and readable curves.
_COLUMN = 252.0
_ROW = 142.0


def has_generated_layout(graph: WorkflowGraph) -> bool:
    """Recognize the previous generator exactly, never guess at manual intent."""
    legacy = _legacy_layout(graph)
    return (legacy is not graph and not any(edge.bend is not None for edge in graph.connections)
            and all(a.position == b.position for a, b in zip(graph.nodes, legacy.nodes)))


def layout_workflow(graph: WorkflowGraph, *, columns: int = 5) -> WorkflowGraph:
    """Compact rank bands with shared helper lanes and deterministic ordering.

    Branches retain their ranks and joins stay after all predecessors. Longer
    paths wrap into bands; helpers share lanes below their band's solid nodes
    instead of reserving a separate full-height region for every root. Only
    positions change. Invalid/incomplete graphs retain their original drawing.
    """
    dag = runnable_dag(graph)
    topology = read_helper_topology(graph)
    if dag is None or not topology.valid:
        return graph
    columns = max(2, min(8, int(columns)))
    ranks = {dag.task_node_id: 0}
    for step in dag.steps:
        ranks[step.node_id] = 1 + max((ranks[node_id] for node_id in step.predecessors), default=0)
    ranks[dag.result_node_id] = 1 + max((ranks[node_id] for node_id in dag.terminal_node_ids), default=0)
    by_rank: dict[int, list[str]] = {}
    for node_id, rank in ranks.items():
        by_rank.setdefault(rank, []).append(node_id)
    positions: dict[str, Point] = {}
    band_y = 0.0
    for start in range(0, max(ranks.values()) + 1, columns):
        band_ranks = range(start, min(start + columns, max(ranks.values()) + 1))
        solid_rows = max(len(by_rank.get(rank, ())) for rank in band_ranks)
        for rank in band_ranks:
            node_ids = by_rank.get(rank, ())
            for row, node_id in enumerate(node_ids):
                positions[node_id] = Point((rank - start) * _COLUMN, band_y + (row + (solid_rows - len(node_ids)) / 2) * _ROW)
        helpers = [item for item in topology.occurrences if start <= ranks[item.root_step_node_id] < start + columns]
        next_y = band_y + solid_rows * _ROW
        for depth in range(1, max((item.depth for item in helpers), default=0) + 1):
            lane = [item for item in helpers if item.depth == depth]
            occupied: set[tuple[int, int]] = set()
            for helper in lane:
                parent = positions[helper.immediate_parent_node_id]
                # Nearest free slot keeps siblings distinct, shares horizontal
                # space across roots, and prevents helper boxes overlapping.
                candidates = ((row, col) for row in range(len(lane) + 1) for col in range(columns))
                row, col = min((slot for slot in candidates if slot not in occupied),
                               key=lambda slot: (slot[0], abs(slot[1] * _COLUMN - parent.x), slot[1]))
                occupied.add((row, col))
                positions[helper.node_id] = Point(col * _COLUMN, next_y + row * _ROW)
            next_y += (1 + max((row for row, _col in occupied), default=0)) * _ROW
        band_y = next_y + _ROW * 0.6
    center_x = (max(point.x for point in positions.values()) + min(point.x for point in positions.values())) / 2
    center_y = (max(point.y for point in positions.values()) + min(point.y for point in positions.values())) / 2
    return replace(graph, nodes=tuple(replace(node, position=Point(positions[node.node_id].x - center_x,
                                                                 positions[node.node_id].y - center_y))
                                      if node.node_id in positions else node for node in graph.nodes))
