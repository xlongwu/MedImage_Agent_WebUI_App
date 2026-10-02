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
  onSubmit: (goal: string) => Promise<void>;
  projectName: string;
}) {
  const { t } = useI18n();
  const [goal, setGoal] = useState("");
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
      await onSubmit(normalized);
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
