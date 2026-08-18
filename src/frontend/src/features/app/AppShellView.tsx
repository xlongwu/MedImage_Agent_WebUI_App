import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import type { ExecutionMode } from "../../lib/types/pipeline";
import type { ChatMessage } from "../../lib/types/assistant";
import type {
  ImagePlane,
  ImagePreview,
  ImageSources,
  ImageValidationReport,
} from "../../lib/types/image";
import type { ProjectDetail } from "../../lib/types/project";
import type { NativeFullPreprocResponse } from "../../types";
import type { ModelStatus } from "../../lib/types/model";
import type { DatasetSummary } from "../../lib/types/dataset";
import type { AppController } from "../app/useAppController";
import type { ProjectController } from "../projects/useProjectController";
import type { TaskController } from "../tasks/useTaskController";
import type { ThemePreference } from "../../hooks/useAppState";
import type { LocalePreference } from "../../hooks/useAppState";
import { getLatestNativeFullPreprocessingRun } from "../../lib/api/preprocessing";
import { hasNativePreprocessingRunEvidence } from "../../lib/projectWorkflow";
import type { ProjectInventory } from "../../lib/projectWorkflow";
import type {
  ArtifactSelection,
  DataSeriesSelection,
  PlanNodeSelection,
  WorkspaceSelectionContext,
} from "../../lib/workspaceSelection";
import { TopBar, WorkspaceSuspenseFallback } from "../dashboard/DashboardChrome";
import { ProjectCreateSheet } from "../projects/ProjectCreateSheet";
import { ProjectsPage } from "../projects/ProjectsPage";
import { ProjectSidebar } from "../projects/ProjectSidebar";
import { DataConversionWorkspace } from "../workspaces/DataConversionWorkspace";
import type { BidsValidationViewState } from "../../components/BidsValidationPanel";
import { PlanWorkspace } from "../workspaces/PlanWorkspace";
import { PreprocessingWorkspace } from "../workspaces/PreprocessingWorkspace";
import { RunsWorkspace } from "../workspaces/RunsWorkspace";
import { OverviewWorkspace } from "../workspaces/OverviewWorkspace";
import { ProjectCreateResultPanel } from "./ProjectCreateResultPanel";
import { RunActivityBar } from "../tasks/RunActivityBar";
import { useProjectRunTasks } from "../runs/useProjectRunTasks";
import { MedicalImageViewer } from "./MedicalImageViewer";
import { AssistantSheet } from "../tools/AssistantSheet";
import { AssistantDock } from "../tools/AssistantDock";
import { ContextInspector } from "../tools/ContextInspector";
import { AppShell } from "../../layouts/AppShell";
import { ProjectShell } from "../../layouts/ProjectShell";
import { GlobalNavigationRail } from "../navigation/GlobalNavigationRail";
import type { AppLocation, LegacyWorkspace, ProjectWorkspace } from "../navigation/workspaceModel";
import { buildLifecycleItems } from "../navigation/workspaceModel";
import { workspaceChromePresetForLocation } from "../../lib/workspaceChromeModel";
import { AgentWorkspace } from "../agent/AgentWorkspace";
import type { AgentTaskController } from "../agent/useAgentTaskController";
import {
  AgentAttentionDialog,
  useAgentAttentionDialog,
} from "../agent/components/AgentAttentionDialog";
import { useI18n } from "../../i18n/useI18n";
import styles from "./AppShellView.module.css";

const QCReportsWorkspace = lazy(() =>
  import("../workspaces/QCReportsWorkspace").then((module) => ({
    default: module.QCReportsWorkspace,
  })),
);
const ResultsWorkspace = lazy(() =>
  import("../workspaces/ResultsWorkspace").then((module) => ({ default: module.ResultsWorkspace })),
);
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
  bidsValidation: BidsValidationViewState & { reload: () => Promise<unknown> };
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
  taskController: Pick<
    TaskController,
    | "tasks"
    | "tasksLoading"
    | "tasksError"
    | "reloadTasks"
    | "selectedTask"
    | "taskEvents"
    | "taskEventsLoading"
    | "taskEventsError"
    | "reloadTaskEvents"
    | "taskDiagnosticsData"
    | "reloadTaskDiagnostics"
    | "taskStreamConnected"
    | "hasPreprocessingRun"
    | "latestPreprocessingRunId"
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
    openLegacyWorkspace: (projectId: string, workspace: LegacyWorkspace) => void;
  };
  image: {
    sequence: string;
    setSequence: (seq: string) => void;
    plane: ImagePlane;
    setPlane: (plane: ImagePlane) => void;
    sliceIndex: number | null;
    setSliceIndex: (index: number | null) => void;
    selectedSubjectId: string | null;
    setSelectedSubjectId: (id: string | null) => void;
    sequenceOptions: string[];
    selectedImageSource: ImageSources["manifest"][number] | null;
    imageSources: { data: ImageSources };
    imageValidation: { data: ImageValidationReport };
    imagePreview: { data: ImagePreview | null; loading: boolean };
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
  onSelectedArtifactChange: (artifact: ArtifactSelection | null) => void;
  onSelectedDataSeriesChange: (selection: DataSeriesSelection | null) => void;
  onSelectedPlanNodeChange: (node: PlanNodeSelection | null) => void;
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
  bidsValidation,
  projectController,
  taskController,
  taskStream,
  app,
  appState,
  navigation,
  image,
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
  onSelectedArtifactChange,
  onSelectedDataSeriesChange,
  onSelectedPlanNodeChange,
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
  const [reviewedPlanSelection, setReviewedPlanSelection] = useState<{
    projectId: string;
    reviewedPlanId: string;
  } | null>(null);
  const [nativeRunState, setNativeRunState] = useState<{
    projectId: string;
    run: NativeFullPreprocResponse | null;
  }>({ projectId: "", run: null });
  const latestNativePreprocessingRun =
    nativeRunState.projectId === selectedProjectId ? nativeRunState.run : null;
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
  const selectedProject = selectedProjectId ? project : null;
  const selectedReviewedPlanId =
    reviewedPlanSelection?.projectId === selectedProjectId
      ? reviewedPlanSelection.reviewedPlanId
      : null;
  const projectDir =
    typeof selectedProject?.data.metadata?.project_dir === "string"
      ? selectedProject.data.metadata.project_dir
      : null;
  const workflowLabels: Record<ProjectWorkspace | LegacyWorkspace, string> = {
    agent: t("nav.agent"),
    overview: t("nav.overview"),
    data: t("nav.data"),
    plan: t("nav.plan"),
    preprocessing: t("nav.preprocessing"),
    runs: t("nav.runs"),
    qc: t("nav.qc"),
    results: t("nav.results"),
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
  const showImageViewer =
    activeWorkspace === "results" && Boolean(projectInventory?.hasConvertedData);
  const persistedPreprocessingRunId =
    typeof selectedProject?.data.metadata?.latest_preprocessing_run_id === "string"
      ? selectedProject.data.metadata.latest_preprocessing_run_id
      : null;
  const hasPreprocessingRun =
    taskController.hasPreprocessingRun ||
    Boolean(persistedPreprocessingRunId) ||
    hasNativePreprocessingRunEvidence(latestNativePreprocessingRun);
  const latestPreprocessingRunId =
    taskController.latestPreprocessingRunId ||
    persistedPreprocessingRunId ||
    (hasNativePreprocessingRunEvidence(latestNativePreprocessingRun)
      ? (latestNativePreprocessingRun?.run_id ?? null)
      : null);
  const lifecycleItems = useMemo(
    () =>
      buildLifecycleItems({
        activeWorkspace: activeWorkspace ?? "overview",
        dataState: projectInventory?.dataState,
        hasPreprocessingRun,
      }),
    [activeWorkspace, hasPreprocessingRun, projectInventory?.dataState],
  );
  const hasSystemMessages = Boolean(
    app.notice ||
    projectController.projectCreateResult ||
    projectController.projectCreateLoading ||
    projectController.projectCreateError ||
    taskStream.error,
  );
  const assistantDocked = wideWorkspace && activeWorkspace === "overview";

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

  useEffect(() => {
    if (!selectedProjectId) return;

    let cancelled = false;

    const refreshLatestNativeRun = () => {
      void getLatestNativeFullPreprocessingRun(baseUrl, selectedProjectId)
        .then((response) => {
          if (!cancelled && response?.run_id) {
            setNativeRunState({ projectId: selectedProjectId, run: response });
          }
        })
        .catch(() => {
          // Native preprocessing is optional for new projects; keep the shell quiet.
        });
    };

    refreshLatestNativeRun();
    const intervalId = window.setInterval(refreshLatestNativeRun, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [baseUrl, selectedProjectId]);

  return (
    <AppShell
      preset={chromePreset}
      rail={
        <GlobalNavigationRail
          location={navigation.location}
          onOpenLegacyWorkspace={navigation.openLegacyWorkspace}
          onOpenProjects={navigation.openProjects}
          onOpenWorkspace={navigation.openWorkspace}
          projectId={selectedProjectId}
        />
      }
      contextSidebar={
        selectedProjectId && (activeWorkspace === "overview" || activeWorkspace === "agent") ? (
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
                onOpenLegacyWorkspace={(workspace) => {
                  if (selectedProjectId) {
                    navigation.openLegacyWorkspace(selectedProjectId, workspace);
                  }
                }}
                onOpenReviewedPlan={(reviewedPlanId) => {
                  if (!selectedProjectId) return;
                  setReviewedPlanSelection({ projectId: selectedProjectId, reviewedPlanId });
                  navigation.openLegacyWorkspace(selectedProjectId, "plan");
                }}
                onOpenRuns={() => {
                  if (selectedProjectId) navigation.openWorkspace(selectedProjectId, "runs");
                }}
                onReopenAttention={attention.reopen}
                projectName={projectInventory?.projectName ?? project.data.name}
              />
            ) : activeWorkspace === "overview" ? (
              <OverviewWorkspace
                agentTask={agentTaskController.task}
                dataset={dataset}
                inventory={projectInventory}
                lifecycleItems={lifecycleItems}
                model={model}
                onSelectedPlanNodeChange={onSelectedPlanNodeChange}
                project={project.data}
                tasks={runTasks}
                onNavigate={(workspace) => {
                  if (!selectedProjectId) return;
                  if (workspace === "runs" || workspace === "settings" || workspace === "agent") {
                    navigation.openWorkspace(selectedProjectId, workspace);
                  } else {
                    navigation.openLegacyWorkspace(selectedProjectId, workspace);
                  }
                }}
              />
            ) : activeWorkspace === "data" ? (
              <DataConversionWorkspace
                baseUrl={baseUrl}
                projectId={selectedProjectId}
                inventory={projectInventory}
                bidsValidation={bidsValidation}
                onSelectedDataSeriesChange={onSelectedDataSeriesChange}
                onOpenAgent={() =>
                  selectedProjectId && navigation.openWorkspace(selectedProjectId, "agent")
                }
              />
            ) : activeWorkspace === "plan" ? (
              <PlanWorkspace
                baseUrl={baseUrl}
                projectId={selectedProjectId}
                selectedProject={selectedProject?.data ?? null}
                projectConfigPath={selectedProject?.data.metadata?.project_config_path}
                datasetIndexPath={selectedProject?.data.metadata?.dataset_index_path}
                rawdataDir={selectedProject?.data.metadata?.rawdata_dir}
                projectDir={projectDir}
                initialPresetDraft={app.presetPlanDraft}
                reviewedPlanId={selectedReviewedPlanId}
                onSelectedNodeChange={onSelectedPlanNodeChange}
                onOpenDataConversion={() =>
                  selectedProjectId && navigation.openLegacyWorkspace(selectedProjectId, "data")
                }
                onOpenEnvironment={() =>
                  selectedProjectId && navigation.openWorkspace(selectedProjectId, "settings")
                }
              />
            ) : activeWorkspace === "preprocessing" ? (
              <PreprocessingWorkspace
                baseUrl={baseUrl}
                projectId={selectedProjectId}
                dataState={projectInventory?.dataState ?? "raw_dicom"}
                inventory={projectInventory}
                hasPreprocessingRun={hasPreprocessingRun}
                preprocessingRunId={latestPreprocessingRunId}
                onOpenAgent={() =>
                  selectedProjectId && navigation.openWorkspace(selectedProjectId, "agent")
                }
                onOpenDataConversion={() =>
                  selectedProjectId && navigation.openLegacyWorkspace(selectedProjectId, "data")
                }
                onOpenToolsDrawer={() => app.setDrawerOpen(true)}
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
            ) : activeWorkspace === "qc" ? (
              <QCReportsWorkspace baseUrl={baseUrl} projectId={selectedProjectId} />
            ) : activeWorkspace === "results" ? (
              <ResultsWorkspace
                baseUrl={baseUrl}
                projectId={selectedProjectId}
                onSelectedArtifactChange={onSelectedArtifactChange}
                viewer={
                  showImageViewer ? (
                    <MedicalImageViewer
                      project={project.data}
                      sequence={image.sequence}
                      plane={image.plane}
                      sequenceOptions={image.sequenceOptions}
                      imageSources={image.imageSources.data}
                      validation={image.imageValidation.data}
                      subjectId={image.selectedSubjectId}
                      preview={image.imagePreview.data}
                      sourceFile={image.selectedImageSource}
                      loading={image.imagePreview.loading}
                      dataState={projectInventory?.dataState}
                      onSequenceChange={image.setSequence}
                      onPlaneChange={image.setPlane}
                      onSubjectChange={image.setSelectedSubjectId}
                      onSliceChange={image.setSliceIndex}
                    />
                  ) : undefined
                }
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
