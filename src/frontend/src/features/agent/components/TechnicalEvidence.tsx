import { Badge } from "../../../components/ui";
import { useI18n } from "../../../i18n/useI18n";
import type { AgentTaskTechnicalDetails } from "../../../lib/types/agentTask";
import styles from "../AgentWorkspace.module.css";

export function TechnicalEvidence({ details }: { details: AgentTaskTechnicalDetails }) {
  const { t } = useI18n();
  const gate = (value: boolean | null | undefined) =>
    value == null ? null : value ? t("common.on") : t("common.off");
  const rows = [
    [t("agent.technical.lifecycle"), details.lifecycle_id],
    [t("agent.technical.internalState"), details.internal_state],
    [t("agent.technical.planHash"), details.plan_hash],
    [t("agent.technical.goalHash"), details.goal_hash],
    [t("agent.technical.ticket"), details.ticket_id],
    [t("agent.technical.run"), details.run_id],
    [t("agent.technical.observation"), details.observation_id],
    [t("agent.technical.evaluation"), details.evaluation_id],
    [t("agent.technical.memoryHash"), details.memory_context_hash],
    [t("agent.technical.memoryPolicy"), details.memory_retrieval_policy_version],
    [t("agent.technical.memoryStatus"), details.memory_status],
    [
      t("agent.technical.memoryUsedBytes"),
      details.memory_used_bytes == null ? null : String(details.memory_used_bytes),
    ],
    [
      t("agent.technical.memoryOmitted"),
      details.memory_omitted_count == null ? null : String(details.memory_omitted_count),
    ],
    [t("agent.technical.memoryAvailable"), gate(details.memory_available)],
    [t("agent.technical.memoryGeneration"), gate(details.memory_generate_enabled)],
    [t("agent.technical.memoryUse"), gate(details.memory_use_enabled)],
  ];

  return (
    <section className={styles.technicalEvidence} aria-label={t("agent.technical.title")}>
      <div className={styles.technicalHeader}>
        <div>
          <h3>{t("agent.technical.title")}</h3>
          <p>{t("agent.technical.description")}</p>
        </div>
        <div>
          <dt>{t("agent.technical.memoryRefs")}</dt>
          <dd>
            {(details.memory_refs ?? [])
              .map((item) => String(item.memory_id ?? ""))
              .filter(Boolean)
              .join(" · ") || t("common.unavailable")}
          </dd>
        </div>
        {(details.memory_warnings ?? []).length ? (
          <div>
            <dt>{t("agent.technical.memoryWarnings")}</dt>
            <dd>{details.memory_warnings?.join(" · ")}</dd>
          </div>
        ) : null}
        <Badge tone="warning">{t("agent.advancedMode")}</Badge>
      </div>
      <dl>
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value || t("common.unavailable")}</dd>
          </div>
        ))}
        <div>
          <dt>{t("agent.technical.nodes")}</dt>
          <dd>{details.node_ids.join(" · ") || t("common.unavailable")}</dd>
        </div>
        {details.backend ? (
          <>
            <div>
              <dt>{t("agent.technical.backend")}</dt>
              <dd>
                {details.backend.requested} → {details.backend.selected ?? t("common.unavailable")}
              </dd>
            </div>
            <div>
              <dt>{t("agent.technical.fallback")}</dt>
              <dd>{details.backend.fallback_reason ?? t("agent.technical.noFallback")}</dd>
            </div>
          </>
        ) : null}
      </dl>
    </section>
  );
}
