import type { ExecutionGraphResponse } from "../../lib/types/executionGraph";
import { useI18n } from "../../i18n/useI18n";
import styles from "./ExecutionGraphView.module.css";

export function ExecutionGraphTable({
  graph,
  onSelect,
}: {
  graph: ExecutionGraphResponse;
  onSelect: (id: string) => void;
}) {
  const { t } = useI18n();
  return (
    <div className={styles.tableWrap}>
      <table>
        <caption>{t("executionGraph.tableCaption")}</caption>
        <thead>
          <tr>
            <th>{t("executionGraph.node")}</th>
            <th>{t("executionGraph.state")}</th>
            <th>{t("executionGraph.dependencies")}</th>
            <th>{t("executionGraph.subjectColumn")}</th>
          </tr>
        </thead>
        <tbody>
          {graph.nodes.map((node) => (
            <tr key={node.node_id}>
              <td>
                <button type="button" onClick={() => onSelect(node.node_id)}>
                  {node.label}
                </button>
              </td>
              <td>{node.state}</td>
              <td>{node.depends_on.join(", ") || "—"}</td>
              <td>
                {node.subject_summary
                  ? `${node.subject_summary.succeeded}/${node.subject_summary.total ?? node.subject_summary.observed}`
                  : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
