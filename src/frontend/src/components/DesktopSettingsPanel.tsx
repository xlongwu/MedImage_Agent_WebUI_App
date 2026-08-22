import { useCallback, useEffect, useState } from "react";
import { useI18n } from "../i18n/useI18n";
import { getDesktopConfig, getDesktopHealth, saveDesktopConfig } from "../lib/api/desktop";
import { JsonBlock } from "./JsonBlock";

type Props = {
  baseUrl: string;
};

type Settings = {
  project_dir: string;
  python_path: string;
  matlab_command: string;
  spm_dir: string;
  dpabi_dir: string;
  gpu_mode: string;
};

const EMPTY_SETTINGS: Settings = {
  project_dir: ".",
  python_path: "",
  matlab_command: "matlab",
  spm_dir: "./third_party/spm12",
  dpabi_dir: "./third_party/DPABI_V8.2_240510",
  gpu_mode: "prefer",
};

export default function DesktopSettingsPanel({ baseUrl }: Props) {
  const { t } = useI18n();
  const [settings, setSettings] = useState<Settings>(EMPTY_SETTINGS);
  const [health, setHealth] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const desktopRuntime = window.MEDIMAGE_DESKTOP_RUNTIME || window.medimageDesktop?.runtime || null;

  const refresh = useCallback(async () => {
    setError("");
    try {
      const [configPayload, healthPayload] = await Promise.all([
        getDesktopConfig(baseUrl),
        getDesktopHealth(baseUrl),
      ]);
      setSettings({ ...EMPTY_SETTINGS, ...((configPayload.config as Partial<Settings>) || {}) });
      setHealth(healthPayload);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [baseUrl]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- Load the controlled desktop configuration when the endpoint changes.
    void refresh();
  }, [refresh]);

  async function save() {
    setSaving(true);
    setError("");
    try {
      await saveDesktopConfig(baseUrl, settings as unknown as Record<string, unknown>);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  function update<K extends keyof Settings>(key: K, value: Settings[K]) {
    setSettings((current) => ({ ...current, [key]: value }));
  }

  return (
    <div>
      {error ? <div className="errorBox">{error}</div> : null}
      <div className="formGrid">
        <label>
          {t("settings.desktop.projectDirectory")}
          <input
            value={settings.project_dir}
            onChange={(event) => update("project_dir", event.target.value)}
          />
        </label>
        <label>
          {t("settings.desktop.pythonPath")}
          <input
            value={settings.python_path}
            onChange={(event) => update("python_path", event.target.value)}
          />
        </label>
        <label>
          {t("settings.desktop.matlabCommand")}
          <input
            value={settings.matlab_command}
            onChange={(event) => update("matlab_command", event.target.value)}
          />
        </label>
        <label>
          {t("settings.desktop.spmDirectory")}
          <input
            value={settings.spm_dir}
            onChange={(event) => update("spm_dir", event.target.value)}
          />
        </label>
        <label>
          {t("settings.desktop.dpabiDirectory")}
          <input
            value={settings.dpabi_dir}
            onChange={(event) => update("dpabi_dir", event.target.value)}
          />
        </label>
        <label>
          {t("settings.desktop.gpuMode")}
          <select
            value={settings.gpu_mode}
            onChange={(event) => update("gpu_mode", event.target.value)}
          >
            <option value="prefer">{t("settings.desktop.preferGpu")}</option>
            <option value="require">{t("settings.desktop.requireGpu")}</option>
            <option value="off">{t("settings.desktop.cpuOnly")}</option>
          </select>
        </label>
      </div>
      <div className="row">
        <button onClick={save} disabled={saving}>
          {saving ? t("settings.desktop.saving") : t("settings.desktop.save")}
        </button>
        <button onClick={refresh}>{t("settings.desktop.refresh")}</button>
      </div>
      <h3>{t("settings.desktop.runtime")}</h3>
      <JsonBlock value={desktopRuntime} emptyText={t("settings.desktop.browserMode")} />
      <h3>{t("settings.desktop.healthChecks")}</h3>
      <JsonBlock value={health} emptyText={t("settings.desktop.noHealth")} />
    </div>
  );
}
