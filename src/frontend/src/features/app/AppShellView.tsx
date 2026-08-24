import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import type { ExecutionMode } from "../../lib/types/pipeline";
import type { ChatMessage } from "../../lib/types/assistant";
import type { ProjectDetail } from "../../lib/types/project";
import type { ModelStatus } from "../../lib/types/model";
import type { DatasetSummary } from "../../lib/types/dataset";
import type { AppController } from "../app/useAppController";
import type { ProjectController } from "../projects/useProjectController";
import type { ThemePreference } from "../../hooks/useAppState";
import type { LocalePreference } from "../../hooks/useAppState";
import type { ProjectInventory } from "../../lib/projectWorkflow";
import type { WorkspaceSelectionContext } from "../../lib/workspaceSelection";
import { TopBar, WorkspaceSuspenseFallback } from "../dashboard/DashboardChrome";
import { ProjectCreateSheet } from "../projects/ProjectCreateSheet";
import { ProjectsPage } from "../projects/ProjectsPage";
import { ProjectSidebar } from "../projects/ProjectSidebar";
import { RunsWorkspace } from "../workspaces/RunsWorkspace";
import { ProjectCreateResultPanel } from "./ProjectCreateResultPanel";
import { RunActivityBar } from "../tasks/RunActivityBar";
import { useProjectRunTasks } from "../runs/useProjectRunTasks";
import { AssistantSheet } from "../tools/AssistantSheet";
import { AssistantDock } from "../tools/AssistantDock";
import { ContextInspector } from "../tools/ContextInspector";
import { AppShell } from "../../layouts/AppShell";
import { ProjectShell } from "../../layouts/ProjectShell";
import { GlobalNavigationRail } from "../navigation/GlobalNavigationRail";
import type { AppLocation, ProjectWorkspace } from "../navigation/workspaceModel";
import { workspaceChromePresetForLocation } from "../../lib/workspaceChromeModel";
import { AgentWorkspace } from "../agent/AgentWorkspace";
import type { AgentTaskController } from "../agent/useAgentTaskController";
import {
  AgentAttentionDialog,
  useAgentAttentionDialog,
} from "../agent/components/AgentAttentionDialog";
import { useI18n } from "../../i18n/useI18n";
import styles from "./AppShellView.module.css";

const SettingsEnvironmentWorkspace = lazy(() =>
  import("../workspaces/SettingsEnvironmentWorkspace").then((module) => ({
    default: module.SettingsEnvironmentWorkspace,
  })),
);

export type AppShellViewProps = {
  agentTaskController: AgentTaskController;
  baseUrl: string;
  drawerOpen: boolean;
  health: boolean | null;
  selectedProjectId: string | null;
  onSelectProject: (id: string) => void;
  project: { data: ProjectDetail; reload: () => Promise<ProjectDetail | null> };
  projectInventory: ProjectInventory | null;
  projectController: Pick<
    ProjectController,
    | "projectCreateResult"
    | "projectCreateLoading"
    | "projectCreateError"
    | "setProjectCreateResult"
    | "setProjectCreateError"
    | "projects"
    | "projectsLoading"
    | "projectsError"
    | "handleDeleteProject"
    | "selectProjectDirectory"
    | "createProjectFromDirectoryPath"
  >;
  taskStream: { error: string | null };
  app: Pick<
    AppController,
    | "notice"
    | "setNotice"
    | "apiError"
    | "version"
    | "versionFromBackend"
    | "checkHealth"
    | "handleScrollToPanel"
    | "setDrawerOpen"
    | "handleReconnectTaskStream"
    | "handleAssistantSubmit"
    | "presetPlanDraft"
  >;
  appState: {
    themePreference: ThemePreference;
    setThemePreference: (themePreference: ThemePreference) => void;
    localePreference: LocalePreference;
    setLocalePreference: (localePreference: LocalePreference) => void;
    advancedMode: boolean;
    setAdvancedMode: (enabled: boolean) => void;
  };
  navigation: {
    location: AppLocation;
    openProject: (projectId: string) => void;
    openProjects: () => void;
    openWorkspace: (projectId: string, workspace: ProjectWorkspace) => void;
  };
  assistant: {
    input: string;
    setInput: (input: string) => void;
    loading: boolean;
    error: string;
    messages: ChatMessage[];
    setMessages: (messages: ChatMessage[]) => void;
  };
  executionMode: ExecutionMode;
  externalSmokeApprovedRun: boolean;
  externalSmokeApprovedBy: string;
  model: ModelStatus | null;
  dataset: DatasetSummary | null;
  onToggleDrawer: () => void;
  handleReconnectTaskStream: () => void;
  handleAssistantSubmit: (event: React.FormEvent) => Promise<void>;
  onNewChat: () => void;
  selectedRunId: string | null;
  setSelectedRunId: (id: string | null) => void;
  selectionContext: WorkspaceSelectionContext;
};

export function AppShellView({
  agentTaskController,
  baseUrl,
  drawerOpen,
  health,
  selectedProjectId,
  onSelectProject,
  project,
  projectInventory,
  projectController,
  taskStream,
  app,
  appState,
  navigation,
  assistant,
  executionMode,
  externalSmokeApprovedRun,
  externalSmokeApprovedBy,
  model,
  dataset,
  onToggleDrawer,
  handleReconnectTaskStream,
  handleAssistantSubmit,
  onNewChat,
  selectedRunId,
  setSelectedRunId,
  selectionContext,
}: AppShellViewProps) {
  const { t } = useI18n();
  const attention = useAgentAttentionDialog(agentTaskController, selectedProjectId);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [wideWorkspace, setWideWorkspace] = useState(
    () =>
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(min-width: 1440px)").matches,
  );
  const [projectCreateOpen, setProjectCreateOpen] = useState(false);
  const {
    error: projectRunError,
    loading: projectRunLoading,
    reload: reloadProjectRuns,
    tasks: runTasks,
    historyTasks: runHistoryTasks,
  } = useProjectRunTasks(baseUrl, selectedProjectId);
  const selectedRunTask = useMemo(
    () => runHistoryTasks.find((task) => task.id === selectedRunId) ?? null,
    [runHistoryTasks, selectedRunId],
  );
  const reloadRunTasks = useCallback(async () => {
    await reloadProjectRuns();
  }, [reloadProjectRuns]);
  const workflowLabels: Record<ProjectWorkspace, string> = {
    agent: t("nav.agent"),
    runs: t("nav.runs"),
    settings: t("nav.settings"),
  };
  const activeWorkspace =
    navigation.location.kind !== "projects" ? navigation.location.workspace : null;
  const chromePreset = workspaceChromePresetForLocation(navigation.location);
  const activePageLabel = activeWorkspace ? workflowLabels[activeWorkspace] : t("nav.projects");
  const topBarProjectName =
    navigation.location.kind === "projects"
      ? t("projects.library")
      : (projectInventory?.projectName ?? project.data.name);
  const hasSystemMessages = Boolean(
    app.notice ||
    projectController.projectCreateResult ||
    projectController.projectCreateLoading ||
    projectController.projectCreateError ||
    taskStream.error,
  );
  const assistantDocked = wideWorkspace && activeWorkspace === "agent";

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "j") {
        event.preventDefault();
        setAssistantOpen(true);
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return undefined;
    const media = window.matchMedia("(min-width: 1440px)");
    const sync = () => setWideWorkspace(media.matches);
    sync();
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, []);

  return (
    <AppShell
      preset={chromePreset}
      rail={
        <GlobalNavigationRail
          location={navigation.location}
          onOpenProjects={navigation.openProjects}
          onOpenWorkspace={navigation.openWorkspace}
          projectId={selectedProjectId}
        />
      }
      contextSidebar={
        selectedProjectId && activeWorkspace === "agent" ? (
          <ProjectSidebar
            onCreateProject={() => setProjectCreateOpen(true)}
            onSelectProject={onSelectProject}
            projects={projectController.projects.data}
            selectedProjectId={selectedProjectId}
          />
        ) : undefined
      }
      topBar={
        <TopBar
          health={health}
          apiError={app.apiError}
          onRetry={app.checkHealth}
          projectName={topBarProjectName}
          activePageLabel={activePageLabel}
          onOpenAssistant={() => setAssistantOpen(true)}
          attentionPending={attention.hasAttention}
          onOpenAttention={attention.reopen}
          onOpenInspector={() => app.setDrawerOpen(true)}
          onBackToProjects={navigation.openProjects}
          locale={appState.localePreference}
          onLocaleChange={appState.setLocalePreference}
          version={app.version}
          versionFromBackend={app.versionFromBackend}
        />
      }
      systemMessages={
        hasSystemMessages ? (
          <>
            {app.notice ? (
              <div className={styles.toastLine}>
                {app.notice}
                <button onClick={() => app.setNotice("")}>{t("common.dismiss")}</button>
              </div>
            ) : null}
            <ProjectCreateResultPanel
              result={projectController.projectCreateResult}
              loading={projectController.projectCreateLoading}
              error={projectController.projectCreateError}
              onDismiss={() => {
                projectController.setProjectCreateResult(null);
                projectController.setProjectCreateError("");
              }}
            />
            {taskStream.error ? (
              <div className={styles.streamBanner}>
                {t("shell.taskStreamDisconnected", { error: taskStream.error })}
                <button onClick={handleReconnectTaskStream}>{t("shell.reconnect")}</button>
              </div>
            ) : null}
          </>
        ) : undefined
      }
      mainClassName={styles.workflowMain}
      inspector={
        assistantDocked && assistantOpen ? (
          <AssistantDock
            activePageLabel={activePageLabel}
            error={assistant.error}
            input={assistant.input}
            loading={assistant.loading}
            messages={assistant.messages}
            onClose={() => setAssistantOpen(false)}
            onInput={assistant.setInput}
            onNewChat={onNewChat}
            onSubmit={handleAssistantSubmit}
            projectName={projectInventory?.projectName ?? project.data.name}
          />
        ) : drawerOpen ? (
          <ContextInspector
            activePageLabel={activePageLabel}
            inventory={projectInventory}
            isOpen={true}
            onToggle={onToggleDrawer}
            project={project.data}
            model={model}
            dataset={dataset}
            executionMode={executionMode}
            externalSmokeApprovedRun={externalSmokeApprovedRun}
            externalSmokeApprovedBy={externalSmokeApprovedBy}
            selectionContext={selectionContext}
            onConfigure={() => {
              if (selectedProjectId) navigation.openWorkspace(selectedProjectId, "settings");
            }}
          />
        ) : null
      }
      inspectorOpen={drawerOpen || (assistantDocked && assistantOpen)}
      runActivity={
        <RunActivityBar
          tasks={runTasks}
          selectedTaskId={selectedRunId}
          onSelectTask={(taskId) => {
            setSelectedRunId(taskId);
            if (selectedProjectId) navigation.openWorkspace(selectedProjectId, "runs");
          }}
          onOpenRuns={() => {
            if (selectedProjectId) navigation.openWorkspace(selectedProjectId, "runs");
          }}
        />
      }
    >
      <AssistantSheet
        activePageLabel={activePageLabel}
        error={assistant.error}
        input={assistant.input}
        loading={assistant.loading}
        messages={assistant.messages}
        onInput={assistant.setInput}
        onNewChat={onNewChat}
        onOpenChange={setAssistantOpen}
        onSubmit={handleAssistantSubmit}
        open={assistantOpen && !assistantDocked}
        projectName={projectInventory?.projectName ?? project.data.name}
        selectionContext={selectionContext}
      />

      <ProjectCreateSheet
        error={projectController.projectCreateError}
        loading={projectController.projectCreateLoading}
        onCreate={projectController.createProjectFromDirectoryPath}
        onOpenChange={setProjectCreateOpen}
        onSelectDirectory={projectController.selectProjectDirectory}
        open={projectCreateOpen}
      />

      <AgentAttentionDialog attention={attention} controller={agentTaskController} />

      {navigation.location.kind === "projects" ? (
        <ProjectsPage
          deletingProjectId={null}
          error={projectController.projectsError}
          loading={projectController.projectsLoading}
          onClose={() => undefined}
          onCreateProject={() => setProjectCreateOpen(true)}
          onDeleteProject={projectController.handleDeleteProject}
          onSelectProject={onSelectProject}
          projects={projectController.projects.data}
          selectedProjectId={selectedProjectId}
        />
      ) : (
        <ProjectShell
          overview={null}
          viewer={undefined}
          workspaceLabel={`${activePageLabel} workspace`}
        >
          <Suspense fallback={<WorkspaceSuspenseFallback label="Loading workspace..." />}>
            {activeWorkspace === "agent" ? (
              <AgentWorkspace
                advancedMode={appState.advancedMode}
                controller={agentTaskController}
                inventory={projectInventory}
                onOpenRuns={() => {
                  if (selectedProjectId) navigation.openWorkspace(selectedProjectId, "runs");
                }}
                onReopenAttention={attention.reopen}
                projectName={projectInventory?.projectName ?? project.data.name}
              />
            ) : activeWorkspace === "runs" ? (
              <RunsWorkspace
                agentTask={agentTaskController.task}
                baseUrl={baseUrl}
                projectId={selectedProjectId}
                tasks={runTasks}
                historyTasks={runHistoryTasks}
                loading={projectRunLoading}
                error={projectRunError}
                onRetryTasks={reloadRunTasks}
                selectedTaskId={selectedRunId}
                onSelectTask={setSelectedRunId}
                selectedTask={selectedRunTask}
                onOpenAgent={() => {
                  if (selectedProjectId) navigation.openWorkspace(selectedProjectId, "agent");
                }}
              />
            ) : (
              <SettingsEnvironmentWorkspace
                baseUrl={baseUrl}
                projectId={selectedProjectId}
                themePreference={appState.themePreference}
                onThemePreferenceChange={appState.setThemePreference}
                localePreference={appState.localePreference}
                onLocalePreferenceChange={appState.setLocalePreference}
                advancedMode={appState.advancedMode}
                onAdvancedModeChange={appState.setAdvancedMode}
              />
            )}
          </Suspense>
        </ProjectShell>
      )}
    </AppShell>
  );
}
