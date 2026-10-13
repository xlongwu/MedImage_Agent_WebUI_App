import { useState, type FormEvent } from "react";

import { Button, Card } from "../../../components/ui";
import { useI18n } from "../../../i18n/useI18n";
import styles from "../AgentWorkspace.module.css";

export function GoalComposer({
  dataStateLabel,
  disabled,
  onSubmit,
  projectName,
}: {
  dataStateLabel: string;
  disabled: boolean;
  onSubmit: (goal: string, teamMode: "single" | "team") => Promise<void>;
  projectName: string;
}) {
  const { t } = useI18n();
  const [goal, setGoal] = useState("");
  const [teamMode, setTeamMode] = useState<"single" | "team">("single");
  const [validationError, setValidationError] = useState("");

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const normalized = goal.trim();
    if (!normalized) {
      setValidationError(t("agent.goalRequired"));
      return;
    }
    setValidationError("");
    try {
      await onSubmit(normalized, teamMode);
    } catch {
      // The controller exposes the backend error in the workspace alert.
    }
  };

  return (
    <Card className={styles.goalComposer} role="region" aria-label={t("agent.goalTitle")}>
      <form onSubmit={handleSubmit}>
        <div>
          <h2>{t("agent.goalTitle")}</h2>
          <p>{t("agent.goalDescription")}</p>
          <p className={styles.taskContext}>
            {projectName} · {dataStateLabel}
          </p>
        </div>
        <label className={styles.goalField}>
          <span>{t("agent.goalLabel")}</span>
          <textarea
            disabled={disabled}
            onChange={(event) => setGoal(event.target.value)}
            placeholder={t("agent.goalPlaceholder")}
            rows={4}
            value={goal}
          />
        </label>
        <fieldset className={styles.planningMode}>
          <legend>{t("agent.planningMode.title")}</legend>
          <label>
            <input
              checked={teamMode === "single"}
              disabled={disabled}
              name="agent-planning-mode"
              onChange={() => setTeamMode("single")}
              type="radio"
              value="single"
            />
            <span>
              <strong>{t("agent.planningMode.single")}</strong>
              <small>{t("agent.planningMode.singleDescription")}</small>
            </span>
          </label>
          <label>
            <input
              checked={teamMode === "team"}
              disabled={disabled}
              name="agent-planning-mode"
              onChange={() => setTeamMode("team")}
              type="radio"
              value="team"
            />
            <span>
              <strong>{t("agent.planningMode.team")}</strong>
              <small>{t("agent.planningMode.teamDescription")}</small>
            </span>
          </label>
        </fieldset>
        {validationError ? <p className={styles.inlineError}>{validationError}</p> : null}
        <div className={styles.composerFooter}>
          <small>{t("agent.goalSafety")}</small>
          <Button data-primary-action="true" disabled={disabled} type="submit" variant="primary">
            {disabled ? t("agent.starting") : t("agent.startTask")}
          </Button>
        </div>
      </form>
    </Card>
  );
}
