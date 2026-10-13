import { useCallback, useRef, useState } from "react";

import { Button, Card } from "../../../components/ui";
import type { I18nContextValue } from "../../../i18n/context";
import { useI18n } from "../../../i18n/useI18n";
import { listAgentTaskTeamEvents, listAgentTaskTeamFindings } from "../../../lib/api/agentTasks";
import type {
  AgentTaskTeamEvent,
  AgentTaskTeamEventPage,
  AgentTaskTeamFinding,
  AgentTaskTeamFindingPage,
  AgentTaskTeamSummary,
} from "../../../lib/types/agentTask";
import { formatSystemMessage } from "../systemMessages";
import styles from "../AgentWorkspace.module.css";
import { AgentTeamFindingList } from "./AgentTeamFindingList";

type Props = {
  baseUrl: string;
  mutating?: boolean;
  onContinueSingle?: () => Promise<void>;
  projectId: string;
  summary: AgentTaskTeamSummary;
  taskId: string;
};

export function AgentTeamActivityCard({
  baseUrl,
  mutating = false,
  onContinueSingle,
  projectId,
  summary,
  taskId,
}: Props) {
  const { locale, t } = useI18n();
  const [findings, setFindings] = useState<AgentTaskTeamFinding[]>([]);
  const [events, setEvents] = useState<AgentTaskTeamEvent[]>([]);
  const [findingCursor, setFindingCursor] = useState<string | null>(null);
  const [eventCursor, setEventCursor] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadingFindings, setLoadingFindings] = useState(false);
  const [loadingEvents, setLoadingEvents] = useState(false);
  const [failed, setFailed] = useState(false);
  const requestId = useRef(0);

  const loadActivity = useCallback(async () => {
    const request = ++requestId.current;
    setLoading(true);
    setFailed(false);
    try {
      const [findingPage, eventPage] = await Promise.all([
        listAgentTaskTeamFindings(baseUrl, projectId, taskId),
        listAgentTaskTeamEvents(baseUrl, projectId, taskId),
      ]);
      if (request !== requestId.current) return;
      setFindings(findingPage.items);
      setEvents(eventPage.items);
      setFindingCursor(findingPage.next_cursor);
      setEventCursor(eventPage.next_cursor);
      setLoaded(true);
    } catch {
      if (request === requestId.current) setFailed(true);
    } finally {
      if (request === requestId.current) setLoading(false);
    }
  }, [baseUrl, projectId, taskId]);

  const loadMoreFindings = async () => {
    if (!findingCursor || loadingFindings) return;
    setLoadingFindings(true);
    try {
      const page: AgentTaskTeamFindingPage = await listAgentTaskTeamFindings(
        baseUrl,
        projectId,
        taskId,
        { after: findingCursor },
      );
      setFindings((current) => [...current, ...page.items]);
      setFindingCursor(page.next_cursor);
    } catch {
      setFailed(true);
    } finally {
      setLoadingFindings(false);
    }
  };

  const loadMoreEvents = async () => {
    if (!eventCursor || loadingEvents) return;
    setLoadingEvents(true);
    try {
      const page: AgentTaskTeamEventPage = await listAgentTaskTeamEvents(
        baseUrl,
        projectId,
        taskId,
        { after: eventCursor },
      );
      setEvents((current) => [...current, ...page.items]);
      setEventCursor(page.next_cursor);
    } catch {
      setFailed(true);
    } finally {
      setLoadingEvents(false);
    }
  };

  const statusText = (value: string) =>
    t(`agent.team.status.${value}` as Parameters<I18nContextValue["t"]>[0]);

  return (
    <Card className={styles.teamActivity} aria-label={t("agent.team.title")}>
      <header className={styles.teamActivityHeader}>
        <div>
          <h2>{t("agent.team.title")}</h2>
          <p>{t("agent.team.statusSummary", { status: statusText(summary.status) })}</p>
        </div>
        <code>{t("agent.team.epoch", { epoch: summary.team_epoch })}</code>
      </header>
      <ul className={styles.teamRoleList}>
        {summary.roles.map((role) => (
          <li key={role.reviewer_kind}>
            <strong>
              {t(`agent.team.role.${role.reviewer_kind}` as Parameters<I18nContextValue["t"]>[0])}
            </strong>
            <span>{statusText(role.status)}</span>
            {role.error_code ? (
              <small>{t("agent.team.roleError", { code: role.error_code })}</small>
            ) : null}
          </li>
        ))}
      </ul>
      <dl className={styles.teamActivityStats}>
        <div>
          <dt>{t("agent.team.blockingFindings")}</dt>
          <dd>{summary.blocking_finding_count}</dd>
        </div>
        <div>
          <dt>{t("agent.team.warningFindings")}</dt>
          <dd>{summary.warning_finding_count}</dd>
        </div>
        <div>
          <dt>{t("agent.team.conflicts")}</dt>
          <dd>{summary.conflict_count}</dd>
        </div>
        <div>
          <dt>{t("agent.team.modelCalls")}</dt>
          <dd>
            {t("agent.team.callBudget", { used: summary.budget_used, limit: summary.budget_limit })}
          </dd>
        </div>
      </dl>
      {onContinueSingle && ["NEEDS_ATTENTION", "FAILED"].includes(summary.status) ? (
        <div>
          <p>{t("agent.team.continueSingleDescription")}</p>
          <Button disabled={mutating} onClick={() => void onContinueSingle()} variant="secondary">
            {t("agent.team.continueSingle")}
          </Button>
        </div>
      ) : null}
      <details
        className={styles.teamActivityDetails}
        onToggle={(event) => {
          if (event.currentTarget.open && !loaded && !loading) void loadActivity();
        }}
      >
        <summary>{t("agent.team.activityDetails")}</summary>
        {loading ? <p role="status">{t("common.loading")}</p> : null}
        {failed ? (
          <div role="alert">
            <p>{t("agent.team.activityUnavailable")}</p>
            <Button onClick={() => void loadActivity()} variant="secondary">
              {t("common.retry")}
            </Button>
          </div>
        ) : null}
        {loaded ? (
          <>
            <section aria-label={t("agent.team.findingsTitle")}>
              <h3>{t("agent.team.findingsTitle")}</h3>
              <AgentTeamFindingList findings={findings} />
              {findingCursor ? (
                <Button
                  disabled={loadingFindings}
                  onClick={() => void loadMoreFindings()}
                  variant="secondary"
                >
                  {loadingFindings ? t("common.loading") : t("agent.team.loadMore")}
                </Button>
              ) : null}
            </section>
            <section aria-label={t("agent.team.eventsTitle")}>
              <h3>{t("agent.team.eventsTitle")}</h3>
              {events.length ? (
                <ol className={styles.teamEventList}>
                  {events.map((item) => (
                    <li key={item.event_id}>
                      <span>{formatSystemMessage(t, item.message)}</span>
                      <small>{new Date(item.occurred_at).toLocaleString(locale)}</small>
                    </li>
                  ))}
                </ol>
              ) : (
                <p>{t("agent.team.eventsEmpty")}</p>
              )}
              {eventCursor ? (
                <Button
                  disabled={loadingEvents}
                  onClick={() => void loadMoreEvents()}
                  variant="secondary"
                >
                  {loadingEvents ? t("common.loading") : t("agent.team.loadMore")}
                </Button>
              ) : null}
            </section>
          </>
        ) : null}
      </details>
    </Card>
  );
}
