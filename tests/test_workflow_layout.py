"""Compact layout preserves topology and fits representative desktop Teams."""

from dataclasses import replace
from itertools import combinations

import pytest
from PySide6.QtGui import QPainterPathStroker
from PySide6.QtWidgets import QApplication

from aura.agents.graph_models import (
    ConnectionKind,
    WorkflowConnection,
    WorkflowGraph,
    WorkflowNode,
    WorkflowNodeKind,
)
from aura.agents.identity import AgentScope
from aura.agents.workflow_layout import _legacy_layout, has_generated_layout, layout_workflow
from aura.gui.agents_workflow_canvas import WorkflowScene, WorkflowView
from aura.gui.agents_workflow_node import NodeVisual


def sample_graph(branches=True, helpers=True, length=3):
    nodes = [WorkflowNode("task", WorkflowNodeKind.TASK), WorkflowNode("result", WorkflowNodeKind.AURA_RESULT)]
    edges = []

    def agent(name):
        nodes.append(WorkflowNode(name, WorkflowNodeKind.AGENT, agent_id="shared-agent", assignment=f"Assignment for {name}"))

    def connect(a, b, helper=False):
        edges.append(WorkflowConnection(f"e{len(edges)}", ConnectionKind.SUB_AGENT if helper else ConnectionKind.STEP, a, b, len(edges)))

    previous = ["task"]
    for index in range(length):
        names = [f"step{index}"] + (["branch"] if branches and index == 0 else [])
        for name in names:
            agent(name)
            for source in previous:
                connect(source, name)
        previous = names
    connect(previous[0], "result")
    if helpers:
        for name, parent in (("helper1", "step0"), ("helper2", "step1"), ("nested", "helper1"), ("sibling", "step0")):
            agent(name)
            connect(parent, name, True)
    return WorkflowGraph("layout-test", AgentScope.PROJECT, "Build and review", nodes=tuple(nodes), connections=tuple(edges))


@pytest.mark.parametrize("branches,helpers,length", [(False, False, 3), (True, True, 3), (False, False, 6)])
def test_compact_complete_graph_is_readable_at_desktop_size(branches, helpers, length):
    app = QApplication.instance() or QApplication([])
    graph = sample_graph(branches, helpers, length)
    arranged = layout_workflow(graph)
    assert layout_workflow(arranged) == arranged
    assert replace(arranged, nodes=graph.nodes) == graph
    assert [replace(node, position=original.position) for node, original in zip(arranged.nodes, graph.nodes)] == list(graph.nodes)
    for a, b in combinations(arranged.nodes, 2):
        assert abs(a.position.x - b.position.x) >= 220 or abs(a.position.y - b.position.y) >= 110
    scene = WorkflowScene()
    scene.render_graph(arranged, {node.node_id: NodeVisual(node.node_id, node.kind, node.node_id, node.assignment) for node in arranged.nodes})
    for edge in scene.edge_items.values():
        stroke = QPainterPathStroker()
        stroke.setWidth(4)
        for node in scene.node_items.values():
            if node.node_id not in (edge.source_node_id, edge.target_node_id):
                assert not stroke.createStroke(edge._path).intersects(node.sceneBoundingRect()), (edge.connection_id, node.node_id)
    view = WorkflowView(scene)
    view.resize(1318, 650)  # graph area in the 1350 x 800 editor
    view.show()
    app.processEvents()
    view.fit_to_content()
    app.processEvents()
    assert view.transform().m11() >= .9  # 14px titles remain comfortably legible
    viewport = view.mapToScene(view.viewport().rect()).boundingRect()
    assert viewport.contains(scene.itemsBoundingRect())
    view.close()
    view.deleteLater()


def test_legacy_detection_is_exact_and_manual_routes_are_respected():
    graph = _legacy_layout(sample_graph())
    assert has_generated_layout(graph)
    changed = replace(graph, nodes=(graph.nodes[0].moved_to(7, 8), *graph.nodes[1:]))
    assert not has_generated_layout(changed)
