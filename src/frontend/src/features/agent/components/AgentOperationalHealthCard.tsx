import { useEffect, useState } from "react";

import { Card } from "../../../components/ui";
import { useI18n } from "../../../i18n/useI18n";
import { getAgentOperationalSummary } from "../../../lib/api/agentOperations";
import type { AgentOperationalSummary } from "../../../lib/types/agentOperations";
import styles from "../AgentWorkspace.module.css";

export function AgentOperationalHealthCard({
  baseUrl,
  projectId,
}: {
  baseUrl?: string;
  projectId?: string | null;
}) {
  const { t } = useI18n();
  const [summary, setSummary] = useState<AgentOperationalSummary | null>(null);

  useEffect(() => {
    if (!baseUrl || !projectId) return;
    let active = true;
    void getAgentOperationalSummary(baseUrl, projectId)
      .then((value) => {
        if (active) setSummary(value);
      })
      .catch(() => {
        if (active) setSummary(null);
      });
    return () => {
      active = false;
    };
  }, [baseUrl, projectId]);

  if (!projectId) return null;
  return (
    <Card className={styles.harnessStatus} aria-label={t("agent.operations.title")}>
      <div className={styles.cardHeading}>
        <div>
          <span className={styles.eyebrow}>{t("agent.operations.eyebrow")}</span>
          <h2>{t("agent.operations.title")}</h2>
        </div>
      </div>
      {summary ? (
        <>
          <p>
            {t("agent.operations.states", {
              count: Object.values(summary.lifecycle_state_counts).reduce(
                (total, value) => total + value,
                0,
              ),
              approvals: summary.approval_waiting_count,
            })}
          </p>
          <p>
            {t("agent.operations.calls", {
              success: summary.model_call_counts.success ?? 0,
              failure: summary.model_call_counts.failure ?? 0,
              unknown: summary.model_call_counts.unknown ?? 0,
            })}
          </p>
          {summary.attentions.map((item) => (
            <p key={item.code}>
              {t("agent.operations.attention", { code: item.code, count: item.count })}
            </p>
          ))}
          {summary.truncated ? <p>{t("agent.operations.truncated")}</p> : null}
        </>
      ) : (
        <p>{t("agent.operations.unavailable")}</p>
      )}
    </Card>
  );
}
