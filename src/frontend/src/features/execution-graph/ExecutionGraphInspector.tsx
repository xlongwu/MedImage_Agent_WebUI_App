import type { ExecutionGraphNode } from "../../lib/types/executionGraph";
import { useI18n } from "../../i18n/useI18n";
import styles from "./ExecutionGraphView.module.css";

export function ExecutionGraphInspector({ node }: { node: ExecutionGraphNode | null }) {
  const { t } = useI18n();
  if (!node)
    return (
      <aside className={styles.inspector}>
        <p>{t("executionGraph.noSelection")}</p>
      </aside>
    );
  return (
    <aside className={styles.inspector} aria-live="polite">
      <h4>{node.label}</h4>
      <dl>
        <div>
          <dt>{t("executionGraph.state")}</dt>
          <dd>{node.state}</dd>
        </div>
        <div>
          <dt>{t("executionGraph.backend")}</dt>
          <dd>{node.backend_id}</dd>
        </div>
        <div>
          <dt>{t("executionGraph.dependencies")}</dt>
          <dd>{node.depends_on.join(", ") || t("executionGraph.none")}</dd>
        </div>
        <div>
          <dt>{t("executionGraph.inputsOutputs")}</dt>
          <dd>
            {node.planned_input_count} / {node.planned_output_count}
          </dd>
        </div>
        <div>
          <dt>{t("executionGraph.warningsErrors")}</dt>
          <dd>
            {node.warning_count} / {node.error_count}
          </dd>
        </div>
      </dl>
    </aside>
  );
}
