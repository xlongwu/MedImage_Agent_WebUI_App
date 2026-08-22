import { describe, expect, it } from "vitest";

import type { ExecutionGraphResponse } from "../../../lib/types/executionGraph";
import { layoutExecutionGraph } from "../layoutExecutionGraph";

const graph: ExecutionGraphResponse = {
  schema_version: 1,
  project_id: "p",
  reviewed_plan_id: "plan",
  plan_hash: "hash",
  run_id: null,
  run_state: null,
  run_terminal: false,
  graph_status: "available",
  structure_hash: "structure",
  state_hash: "state",
  generated_at: "2026-01-01T00:00:00Z",
  nodes: [
    {
      node_id: "a",
      label: "A",
      backend_id: "python",
      parallel_level: "project",
      depends_on: [],
      risk: "normal",
      planned_input_count: 0,
      planned_output_count: 0,
      parameter_keys: [],
      state: "pending",
      state_source: "plan",
      started_at: null,
      ended_at: null,
      duration_seconds: null,
      subject_summary: null,
      warning_count: 0,
      error_count: 0,
      actual_output_count: 0,
      current: false,
    },
    {
      node_id: "b",
      label: "B",
      backend_id: "python",
      parallel_level: "project",
      depends_on: ["a"],
      risk: "normal",
      planned_input_count: 0,
      planned_output_count: 0,
      parameter_keys: [],
      state: "pending",
      state_source: "plan",
      started_at: null,
      ended_at: null,
      duration_seconds: null,
      subject_summary: null,
      warning_count: 0,
      error_count: 0,
      actual_output_count: 0,
      current: false,
    },
  ],
  edges: [{ edge_id: "a->b", source_node_id: "a", target_node_id: "b", state: "pending" }],
  current_node_ids: [],
  ready_node_ids: [],
  terminal_nodes: 0,
  total_nodes: 2,
  node_completion_percent: null,
  warnings: [],
  errors: [],
};

describe("layoutExecutionGraph", () => {
  it("uses stable left-to-right positions for a dependency", () => {
    const first = layoutExecutionGraph(graph);
    const second = layoutExecutionGraph(graph);
    expect(first).toEqual(second);
    expect(first.find((node) => node.id === "b")!.position.x).toBeGreaterThan(
      first.find((node) => node.id === "a")!.position.x,
    );
  });

  it("lays out a 100-node chain without dropping or duplicating nodes", () => {
    const nodes = Array.from({ length: 100 }, (_, index) => ({
      ...graph.nodes[0],
      node_id: `node-${index}`,
      label: `Node ${index}`,
      depends_on: index === 0 ? [] : [`node-${index - 1}`],
    }));
    const edges = nodes.slice(1).map((node, index) => ({
      edge_id: `node-${index}->${node.node_id}`,
      source_node_id: `node-${index}`,
      target_node_id: node.node_id,
      state: "pending" as const,
    }));
    const largeGraph = { ...graph, nodes, edges, total_nodes: nodes.length };

    const layout = layoutExecutionGraph(largeGraph);

    expect(layout).toHaveLength(100);
    expect(new Set(layout.map((node) => node.id)).size).toBe(100);
    expect(layout[99].position.x).toBeGreaterThan(layout[0].position.x);
  });
});
