import { Button } from "../../../components/ui";
import { useI18n } from "../../../i18n/useI18n";
import type { AgentPlanEvidence } from "../../../lib/types/agentTask";

export function PlanEvidenceDetails({
  evidence,
  onClose,
}: {
  evidence: AgentPlanEvidence;
  onClose: () => void;
}) {
  const { t } = useI18n();
  return (
    <section aria-label={t("agent.planEvidence.title")}>
      <h3>{t("agent.planEvidence.title")}</h3>
      {evidence.available ? (
        <>
          <dl>
            <dt>{t("agent.technical.planHash")}</dt>
            <dd>
              <code>{evidence.plan_hash}</code>
            </dd>
            <dt>{t("agent.technical.planRevision")}</dt>
            <dd>{evidence.revision_no}</dd>
            <dt>{t("agent.technical.parentPlan")}</dt>
            <dd>
              <code>{evidence.parent_reviewed_plan_id ?? t("common.unavailable")}</code>
            </dd>
            <dt>{t("agent.technical.revisionReason")}</dt>
            <dd>
              <code>{evidence.revision_reason}</code>
            </dd>
            <dt>{t("agent.planEvidence.scope")}</dt>
            <dd>{evidence.subject_ids.join(" · ") || t("agent.planEvidence.projectScope")}</dd>
            <dt>{t("agent.planEvidence.sessions")}</dt>
            <dd>{evidence.session_ids.join(" · ") || t("common.unavailable")}</dd>
            <dt>{t("agent.planEvidence.include")}</dt>
            <dd>{evidence.include.join(" · ") || t("common.unavailable")}</dd>
            <dt>{t("agent.planEvidence.exclude")}</dt>
            <dd>{evidence.exclude.join(" · ") || t("common.unavailable")}</dd>
            <dt>{t("agent.planEvidence.completeness")}</dt>
            <dd>
              {t(evidence.completeness_required ? "semantic.value.true" : "semantic.value.false")}
            </dd>
          </dl>
          <h4>{t("agent.technical.nodes")}</h4>
          <ol>
            {evidence.nodes.map((node) => (
              <li key={node.node_id}>
                <code>{node.node_id}</code> · <code>{node.backend}</code>
                {node.depends_on.length ? (
                  <span>
                    {" "}
                    · {t("executionGraph.dependencies")}: {node.depends_on.join(" · ")}
                  </span>
                ) : null}
              </li>
            ))}
          </ol>
          <h4>{t("agent.planEvidence.inputs")}</h4>
          {evidence.input_refs.length ? (
            <ul>
              {evidence.input_refs.map((ref) => (
                <li key={`${ref.ref_type}:${ref.ref_id}`}>
                  <code>
                    {ref.ref_type}: {ref.ref_id}
                  </code>{" "}
                  <code>{ref.content_hash}</code>
                </li>
              ))}
            </ul>
          ) : (
            <p>{t("agent.noEvidence")}</p>
          )}
        </>
      ) : (
        <p>
          {t("agent.planEvidence.missing")} <code>{evidence.missing_code}</code>
        </p>
      )}
      <Button onClick={onClose} variant="secondary">
        {t("agent.planEvidence.back")}
      </Button>
    </section>
  );
}
