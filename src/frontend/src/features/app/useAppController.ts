"use client";
import { useCallback, useEffect, useState } from "react";
import type { PresetPlanDraft } from "../../types";
import { DEFAULT_API_BASE, getApiBaseUrl, getHealth, sendAssistantMessage } from "../../lib/api";
import { useTasks } from "../../hooks/useTasks";
import packageMetadata from "../../../package.json";

export interface AppController {
  baseUrl: string;
  setBaseUrl: (url: string) => void;
  health: boolean | null;
  version: string;
  versionFromBackend: boolean;
  apiError: string;
  setApiError: (error: string) => void;
  notice: string;
  setNotice: (notice: string) => void;
  presetPlanDraft: PresetPlanDraft | null;
  setPresetPlanDraft: (draft: PresetPlanDraft | null) => void;
  drawerOpen: boolean;
  setDrawerOpen: (open: boolean) => void;
  checkHealth: () => Promise<void>;
  handleScrollToPanel: (panelId: string) => void;
  handleReconnectTaskStream: (
    taskId: string | null,
    setActiveTaskId: (id: string | null) => void,
  ) => void;
  handleAssistantSubmit: (
    projectId: string,
    input: string,
    onReply: (text: string) => void,
    onError: (err: string) => void,
  ) => Promise<void>;
}

export function useAppController(): AppController {
  const [baseUrl, setBaseUrl] = useState(DEFAULT_API_BASE);
  const [health, setHealth] = useState<boolean | null>(null);
  const [version, setVersion] = useState(packageMetadata.version);
  const [versionFromBackend, setVersionFromBackend] = useState(false);
  const [apiError, setApiError] = useState("");
  const [notice, setNotice] = useState("");
  const [presetPlanDraft, setPresetPlanDraft] = useState<PresetPlanDraft | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const tasks = useTasks();

  const checkHealth = useCallback(async () => {
    setApiError("");
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        const result = await getHealth(baseUrl);
        const status = typeof result.status === "string" ? result.status.toLowerCase() : "";
        const connected = status ? status === "ok" || status === "healthy" : Boolean(result);
        setHealth(connected);
        if (typeof result.version === "string" && result.version.trim()) {
          setVersion(result.version.trim());
          setVersionFromBackend(true);
        } else {
          setVersionFromBackend(false);
        }
        if (!connected) {
          setApiError("Backend health check returned a non-ready status.");
        }
        return;
      } catch {
        await new Promise((resolve) => setTimeout(resolve, 450));
      }
    }
    setHealth(false);
    setVersion(packageMetadata.version);
    setVersionFromBackend(false);
    setApiError(
      "Backend disconnected. Start it with:\npython -m uvicorn src.backend.app.main:app --host 127.0.0.1 --port 8000",
    );
  }, [baseUrl]);

  useEffect(() => {
    let active = true;
    getApiBaseUrl()
      .then((url) => {
        if (active) setBaseUrl(url);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void checkHealth();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [checkHealth]);

  const handleScrollToPanel = useCallback((panelId: string) => {
    document.getElementById(panelId)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  const handleReconnectTaskStream = useCallback(
    (taskId: string | null, setActiveTaskId: (id: string | null) => void) => {
      const nextTaskId = taskId;
      if (!nextTaskId) {
        setNotice("Select a task before reconnecting the task stream.");
        return;
      }
      setActiveTaskId(null);
      window.setTimeout(() => setActiveTaskId(nextTaskId), 0);
    },
    [setNotice],
  );

  const handleAssistantSubmit = useCallback(
    async (
      projectId: string,
      input: string,
      onReply: (text: string) => void,
      onError: (err: string) => void,
    ) => {
      if (!input.trim()) return;
      try {
        const response = await sendAssistantMessage({ project_id: projectId, message: input });
        onReply(response.reply);
      } catch (err) {
        onError(err instanceof Error ? err.message : String(err));
      }
    },
    [],
  );

  return {
    baseUrl,
    setBaseUrl,
    health,
    version,
    versionFromBackend,
    apiError,
    setApiError,
    notice,
    setNotice,
    presetPlanDraft,
    setPresetPlanDraft,
    drawerOpen,
    setDrawerOpen,
    checkHealth,
    handleScrollToPanel,
    handleReconnectTaskStream,
    handleAssistantSubmit,
  };
}
