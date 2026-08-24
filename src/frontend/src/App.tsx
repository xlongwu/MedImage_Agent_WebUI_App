import { useCallback, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { useDatasetSummary } from "./hooks/useDatasetSummary";
import { useModelStatus } from "./hooks/useModelStatus";
import { useProject } from "./hooks/useProjects";
import { useProjectOverview } from "./hooks/useProjectOverview";
import { useProjectBidsValidation } from "./hooks/useProjectBidsValidation";
import { useTaskStream } from "./hooks/useTaskStream";
import { useAppState } from "./hooks/useAppState";
import { buildProjectInventory } from "./lib/projectWorkflow";
import type { ChatMessage } from "./lib/types/assistant";
import type { ExecutionMode } from "./lib/types/pipeline";
import type { TaskEvent, TaskStreamMessage } from "./lib/types/task";
import type {
  ArtifactSelection,
  DataSeriesSelection,
  PlanNodeSelection,
} from "./lib/workspaceSelection";
import { fallbackChat } from "./lib/mockData";
import { useAppController } from "./features/app/useAppController";
import { useProjectController } from "./features/projects/useProjectController";
import { useTaskController } from "./features/tasks/useTaskController";
import type { ProjectController } from "./features/projects/useProjectController";
import type { TaskController } from "./features/tasks/useTaskController";
import { AppShellView } from "./features/app/AppShellView";
import { useWorkspaceNavigation } from "./features/navigation/useWorkspaceNavigation";
import { I18nProvider } from "./i18n/I18nProvider";
import { useImageWorkspaceController } from "./features/app/useImageWorkspaceController";
import { useAgentTaskController } from "./features/agent/useAgentTaskController";

export default function App() {
  const appState = useAppState();
  const app = useAppController();
  const navigation = useWorkspaceNavigation();
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const executionMode: ExecutionMode = "simulated";
  const externalSmokeApprovedRun = false;
  const externalSmokeApprovedBy = "";
  const [selectedDataSeries, setSelectedDataSeries] = useState<DataSeriesSelection | null>(null);
  const [selectedPlanNode, setSelectedPlanNode] = useState<PlanNodeSelection | null>(null);
  const [selectedArtifact, setSelectedArtifact] = useState<ArtifactSelection | null>(null);
  const [assistantInput, setAssistantInput] = useState("");
  const [assistantLoading, setAssistantLoading] = useState(false);
  const [assistantError, setAssistantError] = useState("");
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>(fallbackChat);

  const updateSelectedProject = useCallback(
    (projectId: string | null) => {
      setSelectedProjectId(projectId);
      setSelectedDataSeries(null);
      setSelectedPlanNode(null);
      setSelectedArtifact(null);
      setSelectedTaskId(null);
      setSelectedRunId(null);
      if (navigation.location.kind === "project") {
        if (projectId) navigation.openProject(projectId);
        else navigation.openProjects();
      }
    },
    [navigation],
  );
  const openSelectedProject = useCallback(
    (projectId: string) => {
      updateSelectedProject(projectId);
      navigation.openProject(projectId);
    },
    [navigation, updateSelectedProject],
  );
  const projectController = useProjectController(
    selectedProjectId,
    updateSelectedProject,
  ) as ProjectController;
  const taskController = useTaskController(
    selectedTaskId,
    setSelectedTaskId,
    setActiveTaskId,
  ) as TaskController;

  const project = useProject(selectedProjectId);
  const selectedProjectSummary = useMemo(
    () => projectController.projects.data.find((item) => item.id === selectedProjectId) ?? null,
    [projectController.projects.data, selectedProjectId],
  );
  const effectiveProject = useMemo(
    () =>
      project.fromFallback && selectedProjectSummary
        ? { ...project.data, ...selectedProjectSummary }
        : project.data,
    [project.data, project.fromFallback, selectedProjectSummary],
  );
  const activeProjectId = selectedProjectId;
  const activeStudyId =
    !project.fromFallback && project.data.id === selectedProjectId
      ? project.data.study_id
      : (selectedProjectSummary?.study_id ?? null);
  const agentTaskController = useAgentTaskController({
    baseUrl: app.baseUrl,
    projectId: activeProjectId,
  });
  const selectedProjectForPlanReview = useMemo(
    () =>
      selectedProjectId && !project.fromFallback && project.data.id === selectedProjectId
        ? project.data
        : null,
    [selectedProjectId, project],
  );
  const selectedProjectMetadata = selectedProjectForPlanReview?.metadata;
  const projectDiagnostics = useMemo(() => {
    if (projectController.projectCreateResult?.project_id === selectedProjectId) {
      return projectController.projectCreateResult.diagnostics;
    }
    const diagnostics = selectedProjectMetadata?.diagnostics;
    return diagnostics && typeof diagnostics === "object"
      ? (diagnostics as Record<string, unknown>)
      : {};
  }, [projectController.projectCreateResult, selectedProjectId, selectedProjectMetadata]);

  const overview = useProjectOverview(activeStudyId);
  const bidsValidation = useProjectBidsValidation(app.baseUrl, activeProjectId);
  const projectInventory = useMemo(
    () =>
      buildProjectInventory(
        effectiveProject,
        overview.data,
        projectDiagnostics,
        bidsValidation.data,
      ),
    [bidsValidation.data, effectiveProject, overview.data, projectDiagnostics],
  );

  const dataset = useDatasetSummary(activeProjectId);
  const model = useModelStatus(activeProjectId);
  const image = useImageWorkspaceController(activeProjectId, effectiveProject);

  const handleTaskMessage = useCallback(
    (message: TaskStreamMessage) => {
      taskController.updateTaskFromStream(message);
      if (selectedTaskId === message.task_id) {
        const event: TaskEvent = {
          id: Date.now(),
          task_id: message.task_id,
          status: message.status,
          progress: message.progress,
          message: message.message,
          timestamp: message.timestamp,
          result_path: message.result_path,
          source: "websocket",
          metadata: {},
        };
        taskController.taskEventsSetData((current) => [...current, event]);
      }
      app.setNotice(message.message);
      if (
        (message.status === "completed" || message.status === "failed") &&
        selectedTaskId === message.task_id
      ) {
        window.setTimeout(() => {
          taskController.reloadTaskEvents();
          taskController.reloadTaskDiagnostics();
        }, 250);
      }
    },
    [selectedTaskId, taskController, app],
  );

  const taskStream = useTaskStream(activeTaskId, handleTaskMessage);

  const handleReconnectTaskStream = useCallback(() => {
    app.handleReconnectTaskStream(activeTaskId || selectedTaskId, setActiveTaskId);
  }, [activeTaskId, selectedTaskId, app, setActiveTaskId]);

  const handleAssistantSubmit = useCallback(
    async (event: FormEvent) => {
      event.preventDefault();
      const message = assistantInput.trim();
      if (!message) return;
      setAssistantInput("");
      setAssistantError("");
      setAssistantLoading(true);
      setChatMessages((current) => [...current, { role: "user", text: message }]);
      await app.handleAssistantSubmit(
        project.data.id,
        message,
        (text) => setChatMessages((current) => [...current, { role: "assistant", text }]),
        (err) => setAssistantError(err),
      );
      setAssistantLoading(false);
    },
    [project.data.id, assistantInput, app],
  );

  const selectionContext = useMemo(
    () => ({
      artifact: selectedArtifact,
      dataSeries: selectedDataSeries,
      image: {
        plane: image.plane,
        series: image.sequence || null,
        source:
          image.selectedImageSource?.relative_path ?? image.selectedImageSource?.file_path ?? null,
        subjectId: image.selectedSubjectId,
      },
      planNode: selectedPlanNode,
      run: {
        id: agentTaskController.task?.technical_details?.run_id ?? selectedRunId,
        name:
          agentTaskController.task?.goal_summary ?? taskController.selectedTask?.run_name ?? null,
        pipeline: agentTaskController.task
          ? "Agent Task"
          : (taskController.selectedTask?.pipeline ?? null),
        status: agentTaskController.task?.state ?? taskController.selectedTask?.status ?? null,
      },
    }),
    [
      image.plane,
      image.selectedImageSource?.file_path,
      image.selectedImageSource?.relative_path,
      image.selectedSubjectId,
      image.sequence,
      selectedArtifact,
      selectedDataSeries,
      selectedPlanNode,
      selectedRunId,
      agentTaskController.task,
      taskController.selectedTask?.pipeline,
      taskController.selectedTask?.run_name,
      taskController.selectedTask?.status,
    ],
  );

  return (
    <I18nProvider locale={appState.localePreference}>
      <AppShellView
        baseUrl={app.baseUrl}
        drawerOpen={app.drawerOpen}
        health={app.health}
        selectedProjectId={selectedProjectId}
        onSelectProject={openSelectedProject}
        navigation={navigation}
        project={{ ...project, data: effectiveProject }}
        projectInventory={projectInventory}
        projectController={projectController}
        taskStream={taskStream}
        app={app}
        agentTaskController={agentTaskController}
        appState={appState}
        assistant={{
          input: assistantInput,
          setInput: setAssistantInput,
          loading: assistantLoading,
          error: assistantError,
          messages: chatMessages,
          setMessages: setChatMessages,
        }}
        executionMode={executionMode}
        externalSmokeApprovedRun={externalSmokeApprovedRun}
        externalSmokeApprovedBy={externalSmokeApprovedBy}
        model={model.data}
        dataset={dataset.data}
        onToggleDrawer={() => app.setDrawerOpen(!app.drawerOpen)}
        handleReconnectTaskStream={handleReconnectTaskStream}
        handleAssistantSubmit={handleAssistantSubmit}
        onNewChat={() => setChatMessages(fallbackChat)}
        selectedRunId={selectedRunId}
        setSelectedRunId={setSelectedRunId}
        selectionContext={selectionContext}
      />
    </I18nProvider>
  );
}
