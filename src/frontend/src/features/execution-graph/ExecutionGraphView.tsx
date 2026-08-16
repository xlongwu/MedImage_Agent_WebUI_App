import { useEffect, useMemo, useState } from "react";
import {
  Background,
  Controls,
  MarkerType,
  ReactFlow,
  type Edge,
  type NodeTypes,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type { ExecutionGraphNode as GraphNode } from "../../lib/types/executionGraph";
import { useI18n } from "../../i18n/useI18n";
import { ExecutionGraphInspector } from "./ExecutionGraphInspector";
import { ExecutionGraphNode } from "./ExecutionGraphNode";
import { ExecutionGraphSummary } from "./ExecutionGraphSummary";
import { ExecutionGraphTable } from "./ExecutionGraphTable";
import { layoutExecutionGraph } from "./layoutExecutionGraph";
import { useExecutionGraph } from "./useExecutionGraph";
import styles from "./ExecutionGraphView.module.css";

const nodeTypes: NodeTypes = { execution: ExecutionGraphNode };

export function ExecutionGraphView(props: {
  baseUrl: string;
  projectId: string | null;
  reviewedPlanId?: string | null;
  previewPlan?: Record<string, unknown> | null;
  runId?: string | null;
  autoRefresh?: boolean;
  compact?: boolean;
}) {
  const { t } = useI18n();
  const { graph, error, loading, refresh } = useExecutionGraph(props);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  useEffect(() => {
    setSelectedId(null);
  }, [graph?.structure_hash]);
  const nodes = useMemo(
    () => (graph ? layoutExecutionGraph(graph) : []),
    [graph?.structure_hash, graph?.nodes],
  );
  const edges = useMemo<Edge[]>(
    () =>
      graph
        ? graph.edges.map((edge) => ({
            id: edge.edge_id,
            source: edge.source_node_id,
            target: edge.target_node_id,
            animated: edge.state === "active",
            markerEnd: { type: MarkerType.ArrowClosed },
            className: `${styles.edge} ${styles[`edge_${edge.state}`]}`,
          }))
        : [],
    [graph?.structure_hash, graph?.state_hash],
  );
  const selected =
    graph?.nodes.find((node) => node.node_id === selectedId) ?? graph?.nodes[0] ?? null;
  if (!props.projectId)
    return <section className={styles.empty}>{t("executionGraph.selectProject")}</section>;
  if (!props.reviewedPlanId && !props.previewPlan && !props.runId)
    return <section className={styles.empty}>{t("executionGraph.awaitPlan")}</section>;
  return (
    <section className={styles.root} aria-label={t("executionGraph.flow")}>
      {loading && !graph ? <p role="status">{t("executionGraph.loading")}</p> : null}
      {error ? (
        <div className={styles.error} role="status">
          {t("executionGraph.unavailable")}{" "}
          <button type="button" onClick={refresh}>
            {t("executionGraph.refresh")}
          </button>
        </div>
      ) : null}
      {graph ? (
        <>
          <ExecutionGraphSummary graph={graph} />
          {!props.compact ? (
            <div className={styles.canvas}>
              <ReactFlow
                key={graph.structure_hash}
                nodes={nodes}
                edges={edges}
                nodeTypes={nodeTypes}
                nodesDraggable={false}
                nodesConnectable={false}
                deleteKeyCode={null}
                onNodeClick={(_, node) => setSelectedId(node.id)}
                fitView
                fitViewOptions={{ padding: 0.2 }}
                proOptions={{ hideAttribution: true }}
              >
                <Background />
                <Controls showInteractive={false} />
              </ReactFlow>
            </div>
          ) : null}
          {!props.compact ? <ExecutionGraphInspector node={selected} /> : null}
          {!props.compact ? <ExecutionGraphTable graph={graph} onSelect={setSelectedId} /> : null}
        </>
      ) : null}
    </section>
  );
}
