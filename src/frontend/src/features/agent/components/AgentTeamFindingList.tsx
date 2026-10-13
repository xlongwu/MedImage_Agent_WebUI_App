import { useI18n } from "../../../i18n/useI18n";
import type { AgentTaskTeamFinding } from "../../../lib/types/agentTask";
import styles from "../AgentWorkspace.module.css";

export function AgentTeamFindingList({ findings }: { findings: AgentTaskTeamFinding[] }) {
  const { t } = useI18n();
  if (!findings.length) return <p>{t("agent.team.findingsEmpty")}</p>;

  return (
    <ul className={styles.teamFindingList}>
      {findings.map((finding) => (
        <li key={finding.finding_id}>
          <strong>{t(`agent.team.severity.${finding.severity}`)}</strong>
          <span>{t(`agent.team.role.${finding.reviewer_kind}`)}</span>
          <code>{finding.code}</code>
          <small>{t("agent.team.findingRefs", { refs: finding.input_refs.join(" · ") })}</small>
        </li>
      ))}
    </ul>
  );
}
