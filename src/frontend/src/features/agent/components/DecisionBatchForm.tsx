import { formatSystemMessage } from "../systemMessages";
import { useMemo, useState } from "react";

import { Badge, Button } from "../../../components/ui";
import { useI18n } from "../../../i18n/useI18n";
import type { AgentTaskDecisionBatch } from "../../../lib/types/agentTask";
import type { RegisteredScientificResource } from "../../../lib/api/agentSettings";
import styles from "../AgentWorkspace.module.css";

function fieldErrors(details: Record<string, unknown> | undefined): Record<string, string> {
  const fields = details?.fields;
  if (!fields || typeof fields !== "object" || Array.isArray(fields)) return {};
  return Object.fromEntries(
    Object.entries(fields).filter(
      (entry): entry is [string, string] => typeof entry[1] === "string",
    ),
  );
}

export function DecisionBatchForm({
  batch,
  errorDetails,
  mutating,
  onAnswer,
  onRegisterTemplate,
}: {
  batch: AgentTaskDecisionBatch;
  errorDetails?: Record<string, unknown>;
  mutating: boolean;
  onAnswer: (batchId: string, answers: { item_id: string; value: string }[]) => Promise<void>;
  onRegisterTemplate: (
    batchId: string,
    resource: Omit<RegisteredScientificResource, "checksum">,
  ) => Promise<void>;
}) {
  const { t } = useI18n();
  const [answers, setAnswers] = useState<Record<string, string>>(() =>
    Object.fromEntries(batch.items.map((item) => [item.item_id, item.recommended_option ?? ""])),
  );
  const [localErrors, setLocalErrors] = useState<Record<string, string>>({});
  const [template, setTemplate] = useState({ name: "", path: "", license: "", space: "" });
  const inputRequired = batch.items.some(
    (item) =>
      item.readiness === "input_required" ||
      (item.answer_type === "option" && item.required && item.options.length === 0),
  );
  const serverErrors = useMemo(() => fieldErrors(errorDetails), [errorDetails]);
  const question = (item: (typeof batch.items)[number]) => formatSystemMessage(t, item.question);
  const impact = (item: (typeof batch.items)[number]) => formatSystemMessage(t, item.impact);

  const validate = () => {
    const errors: Record<string, string> = {};
    batch.items.forEach((item) => {
      const value = answers[item.item_id]?.trim() ?? "";
      if (item.required && !value) errors[item.item_id] = t("agent.decision.error.required");
      if (value && item.answer_type === "number") {
        const number = Number(value);
        if (!Number.isFinite(number)) errors[item.item_id] = t("agent.decision.error.number");
        else if (item.min_value !== null && number < item.min_value)
          errors[item.item_id] = t("agent.decision.error.minimum", { value: item.min_value });
        else if (item.max_value !== null && number > item.max_value)
          errors[item.item_id] = t("agent.decision.error.maximum", { value: item.max_value });
      }
    });
    setLocalErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const setAnswer = (itemId: string, value: string) => {
    setAnswers((current) => ({ ...current, [itemId]: value }));
    setLocalErrors((current) => {
      const next = { ...current };
      delete next[itemId];
      return next;
    });
  };

  return (
    <form
      className={styles.decisionForm}
      onSubmit={(event) => {
        event.preventDefault();
        if (inputRequired) return;
        if (!validate()) return;
        void onAnswer(
          batch.batch_id,
          batch.items.map((item) => ({
            item_id: item.item_id,
            value: answers[item.item_id] ?? "",
          })),
        ).catch((): void => {});
      }}
    >
      {batch.items.map((item) => {
        const error = localErrors[item.item_id] ?? serverErrors[item.item_id];
        return (
          <fieldset className={styles.decisionOptions} key={item.item_id}>
            <legend>{question(item)}</legend>
            <p>{impact(item)}</p>
            <div className={styles.decisionMeta}>
              {item.source === "memory_suggestion" ? (
                <Badge tone="info">{t("agent.decision.memorySuggestion")}</Badge>
              ) : null}
              {item.recommendation_source ? (
                <small>
                  {t("agent.decision.recommendationSource", { source: item.recommendation_source })}
                </small>
              ) : null}
            </div>
            {item.readiness === "input_required" ? (
              <div>
                <p>{t("agent.template.inputRequired")}</p>
                {item.allowed_actions.includes("register_template") ? (
                  <div className={styles.templateRegistration}>
                    {(["name", "path", "license"] as const).map((key) => (
                      <label key={key}>
                        {t(`settings.agentDefaults.${key}`)}
                        <input
                          aria-label={t(`settings.agentDefaults.${key}`)}
                          value={template[key]}
                          onChange={(event) =>
                            setTemplate((value) => ({ ...value, [key]: event.target.value }))
                          }
                        />
                      </label>
                    ))}
                    <label>
                      {t("settings.agentDefaults.space")}
                      <select
                        aria-label={t("settings.agentDefaults.space")}
                        value={template.space}
                        onChange={(event) =>
                          setTemplate((value) => ({ ...value, space: event.target.value }))
                        }
                      >
                        <option value="">{t("agent.template.selectSpace")}</option>
                        <option value="MNI152">MNI152</option>
                      </select>
                    </label>
                    <Button
                      type="button"
                      disabled={mutating || Object.values(template).some((value) => !value.trim())}
                      onClick={() =>
                        void onRegisterTemplate(batch.batch_id, {
                          name: template.name.trim(),
                          path: template.path.trim(),
                          license: template.license.trim(),
                          space: "MNI152",
                        }).catch(() =>
                          setLocalErrors((value) => ({
                            ...value,
                            [item.item_id]: t("agent.template.registrationFailed"),
                          })),
                        )
                      }
                    >
                      {mutating ? t("agent.working") : t("agent.template.register")}
                    </Button>
                  </div>
                ) : null}
              </div>
            ) : item.answer_type === "option" ? (
              item.options.map((option) => (
                <label key={option.id}>
                  <input
                    checked={answers[item.item_id] === option.id}
                    name={item.item_id}
                    onChange={() => setAnswer(item.item_id, option.id)}
                    type="radio"
                    value={option.id}
                  />
                  <span>
                    <strong>{formatSystemMessage(t, option.label)}</strong>
                    <small>{formatSystemMessage(t, option.description)}</small>
                  </span>
                  {option.recommended ? (
                    <Badge size="sm" tone="info">
                      {t("agent.recommended")}
                    </Badge>
                  ) : null}
                </label>
              ))
            ) : item.answer_type === "boolean" ? (
              <div className={styles.booleanOptions}>
                <label>
                  <input
                    checked={answers[item.item_id] === "true"}
                    name={item.item_id}
                    onChange={() => setAnswer(item.item_id, "true")}
                    type="radio"
                  />
                  {t("agent.decision.booleanTrue")}
                </label>
                <label>
                  <input
                    checked={answers[item.item_id] === "false"}
                    name={item.item_id}
                    onChange={() => setAnswer(item.item_id, "false")}
                    type="radio"
                  />
                  {t("agent.decision.booleanFalse")}
                </label>
              </div>
            ) : item.answer_type === "number" ? (
              <input
                aria-label={question(item)}
                max={item.max_value ?? undefined}
                min={item.min_value ?? undefined}
                onChange={(event) => setAnswer(item.item_id, event.target.value)}
                step="any"
                type="number"
                value={answers[item.item_id] ?? ""}
              />
            ) : (
              <textarea
                aria-label={
                  item.kind === "goal_revision"
                    ? t("agent.goalRevisionLabel")
                    : t("agent.answerLabel")
                }
                onChange={(event) => setAnswer(item.item_id, event.target.value)}
                rows={3}
                value={answers[item.item_id] ?? ""}
              />
            )}
            {error ? <small className={styles.inlineError}>{error}</small> : null}
          </fieldset>
        );
      })}
      <div className={styles.actionFooter}>
        <span>{t("agent.decision.batch.expiry")}</span>
        <Button
          data-agent-action="answer_science_decision"
          data-primary-action="true"
          disabled={mutating || inputRequired}
          type="submit"
          variant="primary"
        >
          {mutating ? t("agent.working") : t("agent.confirmation.decision.confirm")}
        </Button>
      </div>
    </form>
  );
}
