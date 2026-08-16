import { useI18n } from "../../../i18n/useI18n";
import type { SandboxAttempt } from "../../../lib/types/sandbox";

export function SandboxAttemptPanel({ attempts }: { attempts: SandboxAttempt[] }) {
  const { t } = useI18n();
  return (
    <section aria-label={t("runs.sandbox.title")}>
      <h4>{t("runs.sandbox.title")}</h4>
      <p>{t("runs.sandbox.networkNotEnforced")}</p>
      {attempts.length === 0 ? (
        <p>{t("runs.sandbox.empty")}</p>
      ) : (
        <ul>
          {attempts.map((attempt) => (
            <li key={attempt.sandbox_id}>
              <strong>{attempt.node_id}</strong>
              {" — "}
              {t(`runs.sandbox.status.${attempt.status}`)}
              {attempt.result_code ? ` (${attempt.result_code})` : ""}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
