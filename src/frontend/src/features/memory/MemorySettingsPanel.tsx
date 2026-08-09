import { useCallback, useEffect, useState } from "react";

import { Badge, Button, Card, Table } from "../../components/ui";
import { useI18n } from "../../i18n/useI18n";
import { ApiError } from "../../lib/api/client";
import {
  forgetMemoryItem,
  getMemoryConsent,
  listMemoryCandidates,
  listMemoryItems,
  memoryCommandId,
  pinMemoryItem,
  reviewMemoryCandidate,
  restoreMemoryItem,
  setMemoryConsent,
  type MemoryCandidate,
  type MemoryConsentStatus,
  type MemoryItem,
} from "../../lib/api";

export interface MemorySettingsPanelProps {
  baseUrl: string;
  projectId: string | null;
}

export function MemorySettingsPanel({ baseUrl, projectId }: MemorySettingsPanelProps) {
  const { t } = useI18n();
  const [consent, setConsent] = useState<MemoryConsentStatus | null>(null);
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [forgottenItems, setForgottenItems] = useState<MemoryItem[]>([]);
  const [candidates, setCandidates] = useState<MemoryCandidate[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const errorMessage = useCallback(
    (value: unknown, fallback = t("memory.failure")) => {
      if (value instanceof ApiError) {
        if (value.code === "MEMORY_STORE_UNHEALTHY") return t("memory.storeFailure");
        if (value.code === "MEMORY_OUTBOX_PREFLIGHT_FAILED") return t("memory.outboxFailure");
      }
      return value instanceof Error ? value.message : fallback;
    },
    [t],
  );

  const reload = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    setError(null);
    try {
      const options = { baseUrl };
      const [nextConsent, nextItems, nextForgottenItems, nextCandidates] = await Promise.all([
        getMemoryConsent(projectId, options),
        listMemoryItems(projectId, options),
        listMemoryItems(projectId, options, "forgotten"),
        listMemoryCandidates(projectId, options),
      ]);
      setConsent(nextConsent);
      setItems(nextItems.items);
      setForgottenItems(nextForgottenItems.items);
      setCandidates(nextCandidates.items);
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setLoading(false);
    }
  }, [baseUrl, errorMessage, projectId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const updateConsent = async (field: "generate_enabled" | "use_enabled") => {
    if (!projectId || !consent) return;
    setSaving(true);
    setError(null);
    try {
      const next = await setMemoryConsent(
        projectId,
        {
          command_id: memoryCommandId("consent"),
          generate_enabled:
            field === "generate_enabled" ? !consent.generate_enabled : consent.generate_enabled,
          use_enabled: field === "use_enabled" ? !consent.use_enabled : consent.use_enabled,
        },
        { baseUrl },
      );
      setConsent(next);
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setSaving(false);
    }
  };

  const review = async (candidate: MemoryCandidate, accept: boolean) => {
    if (!projectId) return;
    setSaving(true);
    try {
      await reviewMemoryCandidate(
        projectId,
        candidate,
        accept,
        memoryCommandId(accept ? "accept" : "reject"),
        undefined,
        { baseUrl },
      );
      await reload();
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setSaving(false);
    }
  };

  const pin = async (item: MemoryItem) => {
    if (!projectId) return;
    setSaving(true);
    setError(null);
    try {
      await pinMemoryItem(projectId, item, !item.pinned, memoryCommandId("pin"), { baseUrl });
      await reload();
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setSaving(false);
    }
  };

  const restore = async (item: MemoryItem) => {
    if (!projectId) return;
    const summary = window.prompt(t("memory.restoreSummaryPrompt"));
    if (!summary) return;
    const rawValue = window.prompt(t("memory.restoreValuePrompt"), "{}");
    if (!rawValue) return;
    setSaving(true);
    setError(null);
    try {
      const value = JSON.parse(rawValue) as Record<string, unknown>;
      await restoreMemoryItem(projectId, item, value, summary, memoryCommandId("restore"), {
        baseUrl,
      });
      await reload();
    } catch (nextError) {
      setError(errorMessage(nextError, t("memory.invalidRestore")));
    } finally {
      setSaving(false);
    }
  };

  const forget = async (item: MemoryItem) => {
    if (!projectId || !window.confirm(t("memory.forgetConfirm"))) return;
    setSaving(true);
    try {
      await forgetMemoryItem(projectId, item, memoryCommandId("forget"), { baseUrl });
      await reload();
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card id="settings-memory">
      <div>
        <h3>{t("memory.title")}</h3>
        <p>{t("memory.description")}</p>
      </div>
      {!projectId ? <p>{t("memory.noProject")}</p> : null}
      {loading ? <p role="status">{t("memory.loading")}</p> : null}
      {error ? <p role="alert">{error}</p> : null}
      {consent ? (
        <div aria-label={t("memory.controls")}>
          <p>
            <Badge tone={consent.status === "healthy" ? "success" : "warning"}>
              {consent.status === "healthy"
                ? t("memory.healthy")
                : consent.status === "partial"
                  ? t("memory.partial")
                  : consent.status === "failure"
                    ? t("memory.failed")
                    : t("memory.disabled")}
            </Badge>
          </p>
          {consent.status === "partial" ? (
            <p role="status">
              {t("memory.operationalSummary", {
                lag: consent.outbox_lag,
                retry: consent.retry_jobs,
                dead: consent.dead_letter_jobs,
                forget: consent.pending_forget_records,
              })}
            </p>
          ) : null}
          {consent.status === "failure" ? <p role="alert">{t("memory.healthFailure")}</p> : null}
          <Button
            disabled={saving || !consent.generation_available}
            onClick={() => void updateConsent("generate_enabled")}
            variant="secondary"
          >
            {consent.generate_enabled ? t("memory.stopGenerating") : t("memory.startGenerating")}
          </Button>{" "}
          <Button
            disabled={saving || !consent.use_available}
            onClick={() => void updateConsent("use_enabled")}
            variant="secondary"
          >
            {consent.use_enabled ? t("memory.stopUsing") : t("memory.startUsing")}
          </Button>
          <p>{t("memory.retentionNotice")}</p>
        </div>
      ) : null}
      {!loading &&
      projectId &&
      items.length === 0 &&
      forgottenItems.length === 0 &&
      candidates.length === 0 ? (
        <p>{t("memory.empty")}</p>
      ) : null}
      {candidates.length ? (
        <Table caption={t("memory.candidates")}>
          <thead>
            <tr>
              <th>{t("memory.summary")}</th>
              <th>{t("memory.impact")}</th>
              <th>{t("memory.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {candidates.map((candidate) => (
              <tr key={candidate.candidate_id}>
                <td>
                  {candidate.sensitivity === "restricted" || candidate.sensitivity === "rejected"
                    ? t("memory.sensitiveHidden")
                    : candidate.content_text}
                </td>
                <td>{candidate.impact_class}</td>
                <td>
                  <Button disabled={saving} onClick={() => void review(candidate, true)}>
                    {t("memory.accept")}
                  </Button>{" "}
                  <Button
                    disabled={saving}
                    onClick={() => void review(candidate, false)}
                    variant="secondary"
                  >
                    {t("memory.reject")}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      ) : null}
      {items.length ? (
        <Table caption={t("memory.items")}>
          <thead>
            <tr>
              <th>{t("memory.summary")}</th>
              <th>{t("memory.source")}</th>
              <th>{t("memory.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.memory_id}>
                <td>
                  {item.revision.sensitivity === "restricted" ||
                  item.revision.sensitivity === "rejected"
                    ? t("memory.sensitiveHidden")
                    : item.revision.content_text}
                </td>
                <td>{item.sources.map((source) => source.source_ref).join(", ")}</td>
                <td>
                  <Button disabled={saving} onClick={() => void pin(item)} variant="secondary">
                    {item.pinned ? t("memory.unpin") : t("memory.pin")}
                  </Button>{" "}
                  <Button disabled={saving} onClick={() => void forget(item)} variant="secondary">
                    {t("memory.forget")}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      ) : null}
      {forgottenItems.length ? (
        <Table caption={t("memory.forgottenItems")}>
          <thead>
            <tr>
              <th>{t("memory.summary")}</th>
              <th>{t("memory.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {forgottenItems.map((item) => (
              <tr key={item.memory_id}>
                <td>{t("memory.scrubbed")}</td>
                <td>
                  <Button disabled={saving} onClick={() => void restore(item)} variant="secondary">
                    {t("memory.restore")}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      ) : null}
    </Card>
  );
}
