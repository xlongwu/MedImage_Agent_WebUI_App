import { Progress } from "../../../components/ui";
import { useI18n } from "../../../i18n/useI18n";
import type { AgentTaskOutcome, AgentTaskProgress } from "../../../lib/types/agentTask";
import styles from "../AgentWorkspace.module.css";

const phases = ["context", "planning", "execution", "validation", "complete"] as const;

export function TaskProgress({
  outcome,
  planOnly = false,
  progress,
}: {
  outcome: AgentTaskOutcome | null;
  planOnly?: boolean;
  progress: AgentTaskProgress;
}) {
  const { t } = useI18n();
  const knownSubjectProgress =
    progress.total_subjects && progress.completed_subjects != null
      ? Math.round((progress.completed_subjects / progress.total_subjects) * 100)
      : null;
  const percent = progress.percent ?? knownSubjectProgress;

  return (
    <section className={styles.taskProgress} aria-label={t("agent.progress")}>
      <ol>
        {phases.map((phase) => {
          const currentIndex = phases.indexOf(normalizePhase(progress.phase));
          const index = phases.indexOf(phase);
          const canceledBeforeExecution = outcome === "canceled";
          const skipped =
            (planOnly || canceledBeforeExecution) &&
            (phase === "execution" || phase === "validation");
          return (
            <li
              key={phase}
              data-state={
                skipped
                  ? "skipped"
                  : index < currentIndex
                    ? "done"
                    : index === currentIndex
                      ? "current"
                      : "future"
              }
            >
              <span aria-hidden="true" />
              {t(phaseKey(phase))}
              {skipped ? (
                <small>
                  {canceledBeforeExecution
                    ? t("agent.phase.canceledBeforeExecution")
                    : t("agent.phase.skipped")}
                </small>
              ) : null}
            </li>
          );
        })}
      </ol>
      <Progress label={subjectLabel(progress, t)} value={percent} />
    </section>
  );
}

function phaseKey(phase: (typeof phases)[number]) {
  const keys = {
    context: "agent.phase.context",
    planning: "agent.phase.planning",
    execution: "agent.phase.execution",
    validation: "agent.phase.validation",
    complete: "agent.phase.complete",
  } as const;
  return keys[phase];
}

function normalizePhase(phase: AgentTaskProgress["phase"]): (typeof phases)[number] {
  if (phase === "plan_ready") return "planning";
  if (phase === "data_preparation") return "context";
  if (phase === "recovery") return "execution";
  return phase;
}

function subjectLabel(progress: AgentTaskProgress, t: ReturnType<typeof useI18n>["t"]): string {
  if (progress.total_subjects == null) return t("agent.subjectProgressUnknown");
  return t("agent.subjectProgress", {
    completed: progress.completed_subjects ?? 0,
    total: progress.total_subjects,
  });
}
