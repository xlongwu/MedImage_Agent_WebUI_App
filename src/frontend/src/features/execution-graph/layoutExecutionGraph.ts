import { graphlib, layout } from "@dagrejs/dagre";
import type { Node } from "@xyflow/react";

import type { ExecutionGraphResponse } from "../../lib/types/executionGraph";

export const GRAPH_NODE_WIDTH = 220;
export const GRAPH_NODE_HEIGHT = 112;

export function layoutExecutionGraph(
  graph: ExecutionGraphResponse,
  direction: "LR" | "TB" = "LR",
): Node[] {
  const dagreGraph = new graphlib.Graph()
    .setGraph({ rankdir: direction, ranksep: 84, nodesep: 38 })
    .setDefaultEdgeLabel(() => ({}));
  graph.nodes.forEach((node) =>
    dagreGraph.setNode(node.node_id, { width: GRAPH_NODE_WIDTH, height: GRAPH_NODE_HEIGHT }),
  );
  graph.edges.forEach((edge) => dagreGraph.setEdge(edge.source_node_id, edge.target_node_id));
  layout(dagreGraph);
  return graph.nodes.map((node) => {
    const position = dagreGraph.node(node.node_id);
    return {
      id: node.node_id,
      type: "execution",
      position: { x: position.x - GRAPH_NODE_WIDTH / 2, y: position.y - GRAPH_NODE_HEIGHT / 2 },
      data: node,
    };
  });
}
