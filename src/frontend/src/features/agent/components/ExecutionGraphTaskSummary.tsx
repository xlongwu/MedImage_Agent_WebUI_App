import { useExecutionGraph } from "../../execution-graph/useExecutionGraph";
import { Button, Card } from "../../../components/ui";
import { useI18n } from "../../../i18n/useI18n";

export function ExecutionGraphTaskSummary({
  baseUrl,
  onOpenRuns,
  projectId,
  runId,
}: {
  baseUrl: string;
  onOpenRuns: () => void;
  projectId: string | null;
  runId: string | null | undefined;
}) {
  const { t } = useI18n();
  const { graph } = useExecutionGraph({
    baseUrl,
    projectId,
    runId: runId ?? null,
    autoRefresh: true,
  });
  if (!runId || !projectId) return null;
  const label =
    graph?.current_node_ids.length === 1
      ? graph.nodes.find((node) => node.node_id === graph.current_node_ids[0])?.label
      : graph?.current_node_ids.length
        ? t("executionGraph.currentParallel", { count: graph.current_node_ids.length })
        : null;
  return (
    <Card>
      <strong>{t("executionGraph.flow")}</strong>
      <p>
        {label
          ? t("executionGraph.current", { value: label })
          : graph?.run_terminal
            ? t("executionGraph.ended")
            : t("executionGraph.waiting")}
      </p>
      <Button size="sm" variant="secondary" onClick={onOpenRuns}>
        {t("executionGraph.open")}
      </Button>
    </Card>
  );
}
