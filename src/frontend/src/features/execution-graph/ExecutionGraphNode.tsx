import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import type { ExecutionGraphNode as GraphNode } from "../../lib/types/executionGraph";
import { useI18n } from "../../i18n/useI18n";
import styles from "./ExecutionGraphView.module.css";

export function ExecutionGraphNode({ data, selected }: NodeProps<Node<GraphNode, "execution">>) {
  const { t } = useI18n();
  const summary = data.subject_summary;
  const label = `${data.label}: ${data.state}${summary ? `, ${t("executionGraph.subjects", { succeeded: summary.succeeded, total: summary.total ?? summary.observed, running: summary.running })}` : ""}`;
  return (
    <div
      className={styles.node}
      data-state={data.state}
      data-selected={selected ? "true" : "false"}
      tabIndex={0}
      aria-label={label}
    >
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <strong>{data.label}</strong>
      <span>{data.state}</span>
      {summary ? (
        <small>
          {t("executionGraph.subjects", {
            succeeded: summary.succeeded,
            total: summary.total ?? summary.observed,
            running: summary.running,
          })}
        </small>
      ) : (
        <small>{data.backend_id}</small>
      )}
      {data.error_count ? <em>{t("executionGraph.errors", { count: data.error_count })}</em> : null}
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </div>
  );
}
