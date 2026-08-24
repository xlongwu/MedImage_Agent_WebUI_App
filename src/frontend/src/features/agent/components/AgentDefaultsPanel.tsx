import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from "react";

import { Button, Card } from "../../../components/ui";
import { useI18n } from "../../../i18n/useI18n";
import {
  getProjectAgentSettings,
  updateProjectAgentSettings,
  type ProjectAgentSettings,
  type ProjectAgentSettingsUpdate,
} from "../../../lib/api/agentSettings";

type ResourceDraft = { name: string; path: string; license: string };
const emptyResource = (): ResourceDraft => ({ name: "", path: "", license: "" });

export function AgentDefaultsPanel({ baseUrl, projectId }: { baseUrl: string; projectId: string }) {
  const { t } = useI18n();
  const [settings, setSettings] = useState<ProjectAgentSettings | null>(null);
  const [atlas, setAtlas] = useState<ResourceDraft>(emptyResource);
  const [template, setTemplate] = useState<ResourceDraft>(emptyResource);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const next = await getProjectAgentSettings(baseUrl, projectId);
      setSettings(next);
      setAtlas(next.default_atlas ?? emptyResource());
      setTemplate(next.default_template ?? emptyResource());
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [baseUrl, projectId]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void load();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [load]);

  if (!settings) return <Card>{error || t("common.loading")}</Card>;

  const resourcePayload = (draft: ResourceDraft) =>
    draft.path.trim()
      ? { name: draft.name.trim(), path: draft.path.trim(), license: draft.license.trim() }
      : null;
  const updateResource = (
    setter: Dispatch<SetStateAction<ResourceDraft>>,
    key: keyof ResourceDraft,
    value: string,
  ) => setter((current) => ({ ...current, [key]: value }));
  const save = async () => {
    setSaving(true);
    setError("");
    try {
      const payload: ProjectAgentSettingsUpdate = {
        default_atlas: resourcePayload(atlas),
        default_template: resourcePayload(template),
        cpu_policy: settings.cpu_policy,
        compute_policy: settings.compute_policy,
      };
      const next = await updateProjectAgentSettings(baseUrl, projectId, payload);
      setSettings(next);
      setAtlas(next.default_atlas ?? emptyResource());
      setTemplate(next.default_template ?? emptyResource());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card>
      <h3>{t("settings.agentDefaults.title")}</h3>
      <p>{t("settings.agentDefaults.description")}</p>
      {error ? <p role="alert">{error}</p> : null}
      {(["atlas", "template"] as const).map((kind) => {
        const value = kind === "atlas" ? atlas : template;
        const setter = kind === "atlas" ? setAtlas : setTemplate;
        return (
          <fieldset key={kind}>
            <legend>{t(`settings.agentDefaults.${kind}`)}</legend>
            <input
              aria-label={`${t(`settings.agentDefaults.${kind}`)} ${t("settings.agentDefaults.name")}`}
              value={value.name}
              onChange={(event) => updateResource(setter, "name", event.target.value)}
            />
            <input
              aria-label={`${t(`settings.agentDefaults.${kind}`)} ${t("settings.agentDefaults.path")}`}
              value={value.path}
              onChange={(event) => updateResource(setter, "path", event.target.value)}
            />
            <input
              aria-label={`${t(`settings.agentDefaults.${kind}`)} ${t("settings.agentDefaults.license")}`}
              value={value.license}
              onChange={(event) => updateResource(setter, "license", event.target.value)}
            />
          </fieldset>
        );
      })}
      <label>
        {t("settings.agentDefaults.cpu")}
        <select
          value={settings.cpu_policy}
          onChange={(event) =>
            setSettings({
              ...settings,
              cpu_policy: event.target.value as ProjectAgentSettings["cpu_policy"],
            })
          }
        >
          <option value="auto">auto</option>
          <option value="serial">serial</option>
          <option value="process">process</option>
        </select>
      </label>
      <label>
        {t("settings.agentDefaults.compute")}
        <select
          value={settings.compute_policy}
          onChange={(event) =>
            setSettings({
              ...settings,
              compute_policy: event.target.value as ProjectAgentSettings["compute_policy"],
            })
          }
        >
          <option value="auto">auto</option>
          <option value="cpu">cpu</option>
          <option value="gpu">gpu</option>
        </select>
      </label>
      <p>{t("settings.agentDefaults.approvalNote")}</p>
      <Button disabled={saving} onClick={() => void save()}>
        {saving ? t("common.loading") : t("settings.desktop.save")}
      </Button>
    </Card>
  );
}
