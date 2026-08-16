import type { ExecutionGraphResponse } from "../../lib/types/executionGraph";
import { useI18n } from "../../i18n/useI18n";
import styles from "./ExecutionGraphView.module.css";

export function ExecutionGraphSummary({ graph }: { graph: ExecutionGraphResponse }) {
  const { t } = useI18n();
  const current =
    graph.current_node_ids.length === 1
      ? graph.nodes.find((node) => node.node_id === graph.current_node_ids[0])?.label
      : null;
  return (
    <div className={styles.summary} aria-live="polite">
      <span>
        {current
          ? t("executionGraph.current", { value: current })
          : graph.current_node_ids.length
            ? t("executionGraph.currentParallel", { count: graph.current_node_ids.length })
            : t("executionGraph.noRunning")}
      </span>
      <span>
        {graph.node_completion_percent == null
          ? t("executionGraph.notExecuted")
          : t("executionGraph.terminal", {
              completed: graph.terminal_nodes,
              total: graph.total_nodes,
              percent: graph.node_completion_percent,
            })}
      </span>
      <time dateTime={graph.generated_at}>
        {t("executionGraph.updated", { value: new Date(graph.generated_at).toLocaleString() })}
      </time>
    </div>
  );
}
