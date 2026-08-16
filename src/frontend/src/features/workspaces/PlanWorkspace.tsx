import { useEffect, useMemo, useState, type ReactNode } from "react";

import PlanReviewConsole from "../../components/PlanReviewConsole";
import { TechnicalModuleSection } from "../../components/domain/TechnicalModuleSection";
import { Badge, Button, Card, EmptyState } from "../../components/ui";
import { getProjectReviewedPlan } from "../../lib/api/pipeline";
import type { ProjectDetail } from "../../lib/types/project";
import type { PlanNodeSelection } from "../../lib/workspaceSelection";
import type { PresetPlanDraft, ReviewedPlanRecord } from "../../types";
import { WorkspaceHeader } from "../dashboard/DashboardChrome";
import { ExecutionGraphView } from "../execution-graph/ExecutionGraphView";
import styles from "./PlanWorkspace.module.css";
import layoutStyles from "./WorkspaceLayout.module.css";
import { useI18n } from "../../i18n/useI18n";
import type { I18nContextValue } from "../../i18n/context";

export interface PlanWorkspaceProps {
  baseUrl: string;
  projectId: string | null;
  selectedProject: ProjectDetail | null;
  projectConfigPath?: string;
  datasetIndexPath?: string | null;
  rawdataDir?: string;
  projectDir?: string | null;
  initialPresetDraft?: PresetPlanDraft | null;
  reviewedPlanId?: string | null;
  onSelectedNodeChange?: (node: PlanNodeSelection | null) => void;
  onOpenDataConversion: () => void;
  onOpenEnvironment: () => void;
}

type PlanStatus =
  | "needs-project"
  | "needs-config"
  | "draft"
  | "needs-review"
  | "validated"
  | "reviewed-plan-only";
type StepState =
  | "current"
  | "completed"
  | "available"
  | "attention"
  | "pending-evidence"
  | "not-applicable"
  | "locked";

type NormalizedPlanNode = {
  backend: string;
  id: string;
  name: string;
  detail: string;
  dependsOn: string;
  inputs: string[];
  outputs: string[];
  parameters: Array<{ key: string; value: string }>;
  riskReason: string;
  risk: "high" | "approval" | "unknown" | "normal";
};

type ReviewStep = {
  description: string;
  label: string;
  state: StepState;
};

export function PlanWorkspace({
  baseUrl,
  projectId,
  selectedProject,
  projectConfigPath,
  datasetIndexPath,
  rawdataDir,
  initialPresetDraft,
  reviewedPlanId,
  onSelectedNodeChange,
  onOpenDataConversion,
  onOpenEnvironment,
}: PlanWorkspaceProps) {
  const { t } = useI18n();
  const [showTechnicalPlanTools, setShowTechnicalPlanTools] = useState(false);
  const [reviewedPlanRecord, setReviewedPlanRecord] = useState<ReviewedPlanRecord | null>(null);
  const [reviewedPlanLoading, setReviewedPlanLoading] = useState(false);
  const [reviewedPlanError, setReviewedPlanError] = useState("");
  const [reviewedPlanReloadToken, setReviewedPlanReloadToken] = useState(0);

  useEffect(() => {
    if (!projectId || !reviewedPlanId) {
      setReviewedPlanRecord(null);
      setReviewedPlanError("");
      setReviewedPlanLoading(false);
      return;
    }

    let cancelled = false;
    setReviewedPlanLoading(true);
    setReviewedPlanError("");
    setReviewedPlanRecord(null);
    void getProjectReviewedPlan(baseUrl, projectId, reviewedPlanId)
      .then((response) => {
        if (!cancelled) setReviewedPlanRecord(response.reviewed_plan);
      })
      .catch((error) => {
        if (!cancelled) {
          setReviewedPlanError(error instanceof Error ? error.message : String(error));
        }
      })
      .finally(() => {
        if (!cancelled) setReviewedPlanLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [baseUrl, projectId, reviewedPlanId, reviewedPlanReloadToken]);

  const reviewedPlanDraft = useMemo(
    () => (reviewedPlanRecord ? reviewedPlanToDraft(reviewedPlanRecord) : null),
    [reviewedPlanRecord],
  );
  const activeDraft = reviewedPlanId ? reviewedPlanDraft : initialPresetDraft;
  const plan = activeDraft?.plan ?? null;
  const validation = useMemo(() => activeDraft?.validation ?? {}, [activeDraft]);
  const planOnly = isPlanOnlyDraft(activeDraft);
  const nodes = useMemo(() => normalizePlanNodes(plan, validation, t), [plan, t, validation]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(nodes[0]?.id ?? null);
  const status = derivePlanStatus(projectId, selectedProject, projectConfigPath, activeDraft);
  const summary = summarizePlan(status, activeDraft, nodes.length, validation, t);
  const hasProjectContext = Boolean(projectId && selectedProject);
  const validationErrors = arrayCount(validation.errors);
  const validationWarnings = arrayCount(validation.warnings);
  const nextActions = activeDraft?.next_actions ?? [];
  const reviewSteps = planReviewSteps(status, validation, planOnly, t);
  const gateEvidence = summarizeGateEvidence(validation, planOnly, t);
  const selectedNode = nodes.find((node) => node.id === selectedNodeId) ?? nodes[0] ?? null;

  useEffect(() => {
    onSelectedNodeChange?.(
      selectedNode
        ? {
            backend: selectedNode.backend,
            detail: selectedNode.detail,
            id: selectedNode.id,
            name: selectedNode.name,
            risk: riskLabel(selectedNode.risk, t),
          }
        : null,
    );
  }, [onSelectedNodeChange, selectedNode, t]);

  if (reviewedPlanId && reviewedPlanLoading) {
    return (
      <div className={layoutStyles.stack}>
        <WorkspaceHeader
          title={t("plan.title")}
          subtitle={t("plan.subtitle")}
          status={t("plan.loadingReviewed")}
        />
        <Card>
          <p role="status">{t("plan.loadingReviewedDescription")}</p>
        </Card>
      </div>
    );
  }

  if (reviewedPlanId && reviewedPlanError) {
    return (
      <div className={layoutStyles.stack}>
        <WorkspaceHeader
          title={t("plan.title")}
          subtitle={t("plan.subtitle")}
          status={t("plan.reviewedLoadFailed")}
        />
        <EmptyState
          title={t("plan.reviewedLoadFailed")}
          description={reviewedPlanError}
          action={
            <Button onClick={() => setReviewedPlanReloadToken((value) => value + 1)}>
              {t("common.retry")}
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <div className={layoutStyles.stack}>
      <WorkspaceHeader
        title={t("plan.title")}
        subtitle={t("plan.subtitle")}
        status={summary.badge}
      />

      {!hasProjectContext ? (
        <EmptyState
          title={t("plan.selectProjectTitle")}
          description={t("plan.selectProjectDescription")}
          action={<Button onClick={onOpenDataConversion}>{t("plan.openData")}</Button>}
        />
      ) : !projectConfigPath ? (
        <EmptyState
          title={t("plan.configRequired")}
          description={t("plan.configDescription")}
          action={<Button onClick={onOpenEnvironment}>{t("plan.openSettings")}</Button>}
        />
      ) : null}

      <section className={styles.planGrid} aria-label={t("plan.overview")}>
        <Card className={styles.outlineCard} tone="muted">
          <div className={styles.cardHeader}>
            <div>
              <h3>{t("plan.outline")}</h3>
              <p>{t("plan.outlineDescription")}</p>
            </div>
            <Badge tone={summary.tone}>{summary.badge}</Badge>
          </div>
          <dl className={styles.outlineList}>
            <div>
              <dt>{t("plan.goal")}</dt>
              <dd>{activeDraft?.goal || t("plan.noGoal")}</dd>
            </div>
            <div>
              <dt>{t("plan.dataScope")}</dt>
              <dd>
                {selectedProject
                  ? `${selectedProject.name} · ${t("plan.subjectCount", { count: selectedProject.subjects_count })}`
                  : t("plan.selectProject")}
              </dd>
            </div>
            <div>
              <dt>{t("plan.nodes")}</dt>
              <dd>
                {nodes.length ? t("plan.plannedNodes", { count: nodes.length }) : t("plan.noNodes")}
              </dd>
            </div>
            <div>
              <dt>{t("plan.validation")}</dt>
              <dd>{summary.validationText}</dd>
            </div>
            {reviewedPlanRecord ? (
              <div>
                <dt>{t("plan.reviewedPlanId")}</dt>
                <dd>{reviewedPlanRecord.reviewed_plan_id}</dd>
              </div>
            ) : null}
          </dl>
          {nextActions.length ? (
            <div className={styles.nextActions} aria-label={t("plan.nextActions")}>
              <strong>{t("plan.nextActions")}</strong>
              <ul>
                {nextActions.slice(0, 4).map((action) => (
                  <li key={action}>{action}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </Card>

        <Card className={styles.graphCard}>
          <div className={styles.cardHeader}>
            <div>
              <h3>{t("plan.pipelineGraph")}</h3>
              <p>{t("plan.pipelineDescription")}</p>
            </div>
          </div>
          <ExecutionGraphView
            baseUrl={baseUrl}
            projectId={projectId}
            reviewedPlanId={reviewedPlanId}
            previewPlan={reviewedPlanId ? null : plan}
          />
          {/* Maintains the same screen-reader outline while the canonical visual
              representation is the read-only backend graph above. */}
          {nodes.length ? (
            <ol className={styles.visuallyHidden} aria-label={t("plan.pipelineSteps")}>
              {nodes.map((node) => (
                <li key={node.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedNodeId(node.id)}
                    aria-label={t("plan.inspectNode", { name: node.name })}
                  >
                    {node.name}
                  </button>
                  <span>{riskLabel(node.risk, t)}</span>
                </li>
              ))}
            </ol>
          ) : null}
        </Card>

        <Card className={styles.inspectorCard} aria-label={t("plan.inspector")}>
          <div className={styles.cardHeader}>
            <div>
              <h3>{t("plan.inspectorTitle")}</h3>
              <p>{t("plan.inspectorDescription")}</p>
            </div>
          </div>
          <NodeInspector node={selectedNode} />
          <div className={styles.machineHeader}>
            <strong>{t("plan.stateMachine")}</strong>
            <span>{t("plan.backendAuthoritative")}</span>
          </div>
          <ol className={styles.stateList} aria-label={t("plan.stateMachine")}>
            {reviewSteps.map((step) => (
              <li
                aria-label={`${step.label}: ${stepLabel(step.state, t)}`}
                className={styles.stateItem}
                data-state={step.state}
                key={step.label}
              >
                <span>
                  <strong>{step.label}</strong>
                  <small>{step.description}</small>
                </span>
                <Badge tone={stepTone(step.state)} size="sm">
                  {stepLabel(step.state, t)}
                </Badge>
              </li>
            ))}
          </ol>
          <div className={styles.reviewFacts} aria-label={t("plan.reviewFacts")}>
            <div>
              <span>{t("plan.validationErrors")}</span>
              <strong>{validationErrors}</strong>
            </div>
            <div>
              <span>{t("plan.validationNotices")}</span>
              <strong>{validationWarnings}</strong>
            </div>
            <div>
              <span>{t("plan.projectConfig")}</span>
              <strong>{projectConfigPath ? t("plan.registered") : t("plan.missing")}</strong>
            </div>
            <div>
              <span>{t("plan.execution")}</span>
              <strong>{planOnly ? t("plan.notExecuted") : t("plan.backendGated")}</strong>
            </div>
            <div>
              <span>{t("plan.approvalEvidence")}</span>
              <strong>{gateEvidence.approval}</strong>
            </div>
            <div>
              <span>{t("plan.dryRunEvidence")}</span>
              <strong>{gateEvidence.dryRun}</strong>
            </div>
            <div>
              <span>{t("plan.readyEvidence")}</span>
              <strong>{gateEvidence.ready}</strong>
            </div>
            {planOnly ? (
              <>
                <div>
                  <span>{t("plan.capabilityLevel")}</span>
                  <strong>{t("plan.metadataOnly")}</strong>
                </div>
                <div>
                  <span>{t("plan.rawdataState")}</span>
                  <strong>{t("plan.unchanged")}</strong>
                </div>
              </>
            ) : null}
          </div>
        </Card>
      </section>

      <section className={styles.reviewBar} aria-label={t("plan.reviewBar")}>
        <div>
          <strong>{summary.title}</strong>
          <p>{summary.description}</p>
        </div>
        <div className={styles.reviewBarActions}>
          <Button variant="ghost" onClick={onOpenEnvironment}>
            {t("plan.reviewEnvironment")}
          </Button>
          <Button variant="primary" onClick={() => setShowTechnicalPlanTools((value) => !value)}>
            {showTechnicalPlanTools ? t("plan.hideTechnical") : t("plan.openTechnical")}
          </Button>
        </div>
      </section>

      <TechnicalModuleSection
        ariaLabel={t("plan.openTechnical")}
        bodyVisible={showTechnicalPlanTools}
        className={styles.advancedSection}
        description={t("plan.technicalDescription")}
        evidenceLevel="backend_required"
        helperText={
          showTechnicalPlanTools ? t("plan.technicalOpenHelp") : t("plan.technicalClosedHelp")
        }
        safetyNote={planOnly ? t("plan.planOnlySafety") : t("plan.technicalSafety")}
        status={showTechnicalPlanTools ? t("plan.open") : t("plan.onDemand")}
        statusTone="info"
        title={t("plan.openTechnical")}
      >
        <>
          {planOnly && reviewedPlanRecord ? (
            <ReadOnlyReviewedPlan record={reviewedPlanRecord} />
          ) : (
            <PlanReviewConsole
              selectedProjectId={projectId}
              selectedProject={selectedProject}
              projectConfigPath={projectConfigPath}
              datasetIndexPath={datasetIndexPath}
              rawdataDir={rawdataDir}
              initialPresetDraft={activeDraft}
            />
          )}
        </>
      </TechnicalModuleSection>
    </div>
  );
}

function derivePlanStatus(
  projectId: string | null,
  selectedProject: ProjectDetail | null,
  projectConfigPath: string | undefined,
  draft: PresetPlanDraft | null | undefined,
): PlanStatus {
  if (!projectId || !selectedProject) return "needs-project";
  if (!projectConfigPath) return "needs-config";
  if (!draft?.plan) return "draft";
  if (draft.source === "reviewed_plan" && isPlanOnlyDraft(draft)) return "reviewed-plan-only";
  if (draft.validation?.ok === true) return "validated";
  return "needs-review";
}

function summarizePlan(
  status: PlanStatus,
  draft: PresetPlanDraft | null | undefined,
  nodeCount: number,
  validation: Record<string, unknown>,
  t: I18nContextValue["t"],
): {
  badge: string;
  description: string;
  title: string;
  tone: "neutral" | "info" | "success" | "warning" | "danger";
  validationText: string;
} {
  const issueCount = countValidationIssues(validation);
  const validationText =
    draft?.validation?.ok === true
      ? issueCount
        ? t("plan.issueCount", { count: issueCount })
        : t("plan.validationPresent")
      : draft
        ? t("plan.reviewRequired")
        : t("plan.noValidation");

  if (status === "needs-project") {
    return {
      badge: t("plan.needsProject"),
      description: t("plan.waitingProject"),
      title: t("plan.projectContextRequired"),
      tone: "warning",
      validationText,
    };
  }
  if (status === "needs-config") {
    return {
      badge: t("plan.needsConfig"),
      description: t("plan.configMissingDescription"),
      title: t("plan.configMissing"),
      tone: "warning",
      validationText,
    };
  }
  if (status === "reviewed-plan-only") {
    return {
      badge: t("plan.reviewedPlanOnly"),
      description: t("plan.reviewedPlanOnlyDescription", { count: nodeCount }),
      title: t("plan.reviewedPlanLoaded"),
      tone: "success",
      validationText: t("plan.metadataValidated"),
    };
  }
  if (status === "validated") {
    return {
      badge: t("plan.validatedDraft"),
      description: t("plan.validatedDescription", { count: nodeCount }),
      title: t("plan.draftReady"),
      tone: "success",
      validationText,
    };
  }
  if (status === "needs-review") {
    return {
      badge: t("plan.needsReview"),
      description: t("plan.needsReviewDescription"),
      title: t("plan.reviewDraft"),
      tone: "info",
      validationText,
    };
  }
  return {
    badge: t("plan.draft"),
    description: t("plan.draftDescription"),
    title: t("plan.draftMissing"),
    tone: "neutral",
    validationText,
  };
}

function normalizePlanNodes(
  plan: Record<string, unknown> | null,
  validation: Record<string, unknown>,
  t: I18nContextValue["t"],
): NormalizedPlanNode[] {
  const rawNodes = Array.isArray(plan?.nodes) ? plan.nodes : [];
  const highRiskNodes = stringSet(validation.high_risk_nodes);
  const approvalNodes = stringSet(validation.approval_required_nodes);
  const unknownNodes = stringSet(validation.unknown_nodes);

  return rawNodes.map((rawNode, index) => {
    const node = isRecord(rawNode) ? rawNode : {};
    const id = String(node.id ?? node.node_id ?? `node-${index + 1}`);
    const name = String(node.name ?? node.label ?? id);
    const detail = String(
      node.description ?? node.type ?? node.operation ?? t("plan.pipelineNode"),
    );
    const backend = String(node.backend ?? node.runner ?? node.type ?? t("plan.registeredRunner"));
    const dependsOn = Array.isArray(node.depends_on)
      ? node.depends_on.map(String).join(", ") || t("plan.none")
      : t("plan.none");
    const inputs = stringArray(node.inputs);
    const outputs = stringArray(node.outputs);
    const parameters = normalizeParameters(node.params ?? node.parameters, t);
    const risk = highRiskNodes.has(id)
      ? "high"
      : approvalNodes.has(id)
        ? "approval"
        : unknownNodes.has(id)
          ? "unknown"
          : "normal";
    const riskReason =
      risk === "high"
        ? t("plan.riskHighReason")
        : risk === "approval"
          ? t("plan.riskApprovalReason")
          : risk === "unknown"
            ? t("plan.riskUnknownReason")
            : t("plan.riskNormalReason");

    return { backend, id, inputs, name, outputs, parameters, detail, dependsOn, riskReason, risk };
  });
}

function planReviewSteps(
  status: PlanStatus,
  validation: Record<string, unknown>,
  planOnly: boolean,
  t: I18nContextValue["t"],
): ReviewStep[] {
  const hasContext = status !== "needs-project" && status !== "needs-config";
  const hasDraft =
    status === "validated" || status === "needs-review" || status === "reviewed-plan-only";
  const issueCount = countValidationIssues(validation);
  const validationOk = validation.ok === true;

  if (planOnly && hasDraft) {
    return [
      {
        label: t("plan.stepDraft"),
        description: t("plan.reviewedPlanLoaded"),
        state: "completed",
      },
      {
        label: t("plan.stepValidated"),
        description: validationOk ? t("plan.validatorPassed") : t("plan.metadataValidated"),
        state: "completed",
      },
      {
        label: t("plan.stepNeedsReview"),
        description: t("plan.reviewCompleted"),
        state: "completed",
      },
      {
        label: t("plan.stepApproved"),
        description: t("plan.executionApprovalNotRequired"),
        state: "not-applicable",
      },
      {
        label: t("plan.stepDryRun"),
        description: t("plan.dryRunNotRequired"),
        state: "not-applicable",
      },
      {
        label: t("plan.stepReady"),
        description: t("plan.readinessNotRequired"),
        state: "not-applicable",
      },
      {
        label: t("plan.stepExecuted"),
        description: t("plan.planNotExecuted"),
        state: "not-applicable",
      },
    ];
  }
  const approved = approvalEvidenceSignal(validation);
  const dryRunPassed = booleanSignal(validation, [
    "dry_run_passed",
    "dry_run_ok",
    "dry_run_completed",
  ]);
  const readyToExecute = booleanSignal(validation, ["ready_to_execute", "execution_ready"]);
  const executed = booleanSignal(validation, ["executed", "execution_succeeded"]);

  return [
    {
      label: t("plan.stepDraft"),
      description: hasDraft ? t("plan.draftLoaded") : t("plan.generateDraft"),
      state: hasContext ? (hasDraft ? "completed" : "current") : "locked",
    },
    {
      label: t("plan.stepValidated"),
      description: validationOk ? t("plan.validatorPassed") : t("plan.validatorPending"),
      state: validationOk ? "completed" : hasDraft ? "attention" : "locked",
    },
    {
      label: t("plan.stepNeedsReview"),
      description: issueCount
        ? t("plan.issueCount", { count: issueCount })
        : t("plan.humanReviewRequired"),
      state: hasDraft && !approved ? "current" : approved ? "completed" : "locked",
    },
    {
      label: t("plan.stepApproved"),
      description: approved ? t("plan.approvalPassed") : t("plan.awaitingApproval"),
      state: approved ? "completed" : hasDraft ? "pending-evidence" : "locked",
    },
    {
      label: t("plan.stepDryRun"),
      description: dryRunPassed
        ? t("plan.dryRunPresent")
        : approved
          ? t("plan.dryRunPending")
          : t("plan.dryRunNotPassed"),
      state: dryRunPassed ? "completed" : approved ? "pending-evidence" : "locked",
    },
    {
      label: t("plan.stepReady"),
      description: readyToExecute
        ? t("plan.readinessPresent")
        : dryRunPassed
          ? t("plan.readinessPending")
          : t("plan.executionLocked"),
      state: readyToExecute ? "completed" : dryRunPassed ? "pending-evidence" : "locked",
    },
    {
      label: t("plan.stepExecuted"),
      description: executed
        ? t("plan.executionPresent")
        : readyToExecute
          ? t("plan.executionPending")
          : t("plan.noExecution"),
      state: executed ? "completed" : readyToExecute ? "pending-evidence" : "locked",
    },
  ];
}

function summarizeGateEvidence(
  validation: Record<string, unknown>,
  planOnly: boolean,
  t: I18nContextValue["t"],
): {
  approval: string;
  dryRun: string;
  ready: string;
} {
  if (planOnly) {
    return {
      approval: t("plan.notApplicable"),
      dryRun: t("plan.notApplicable"),
      ready: t("plan.notApplicable"),
    };
  }
  return {
    approval: approvalEvidenceSignal(validation) ? t("plan.created") : t("plan.backendRequired"),
    dryRun: booleanSignal(validation, ["dry_run_passed", "dry_run_ok", "dry_run_completed"])
      ? t("plan.created")
      : t("plan.backendRequired"),
    ready: booleanSignal(validation, ["ready_to_execute", "execution_ready"])
      ? t("plan.created")
      : t("plan.backendRequired"),
  };
}

function countValidationIssues(validation: Record<string, unknown>): number {
  return arrayCount(validation.errors) + arrayCount(validation.warnings);
}

function arrayCount(value: unknown): number {
  return Array.isArray(value) ? value.length : 0;
}

function stringSet(value: unknown): Set<string> {
  return new Set(Array.isArray(value) ? value.map(String) : []);
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String).filter(Boolean) : [];
}

function normalizeParameters(
  value: unknown,
  t: I18nContextValue["t"],
): Array<{ key: string; value: string }> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  return Object.entries(value as Record<string, unknown>)
    .slice(0, 8)
    .map(([key, entry]) => ({ key, value: stringifyValue(entry, t) }));
}

function stringifyValue(value: unknown, t: I18nContextValue["t"]): string {
  if (value === null || value === undefined || value === "") return t("plan.notSet");
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return t("plan.complexValue");
  }
}

function approvalEvidenceSignal(record: Record<string, unknown>): boolean {
  return booleanSignal(record, [
    "approved",
    "approval_passed",
    "approval_gate_passed",
    "approval_result",
  ]);
}

function booleanSignal(record: Record<string, unknown>, keys: string[]): boolean {
  for (const key of keys) {
    const value = record[key];
    if (value === true) return true;
    if (value && typeof value === "object" && !Array.isArray(value)) {
      const nested = value as Record<string, unknown>;
      if (
        nested.approved === true ||
        nested.ok === true ||
        nested.passed === true ||
        nested.execution_allowed === true
      ) {
        return true;
      }
    }
  }
  return false;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function reviewedPlanToDraft(record: ReviewedPlanRecord): PresetPlanDraft {
  const payload = record.payload;
  const plan = isRecord(payload.plan) ? payload.plan : {};
  const validation = isRecord(payload.validation) ? payload.validation : {};
  return {
    preset_id: `reviewed:${record.reviewed_plan_id}`,
    project_id: record.project_id,
    goal: typeof payload.goal === "string" ? payload.goal : "",
    plan,
    validation: {
      ...validation,
      reviewed_plan_status: record.status,
      execution_status:
        typeof payload.execution_status === "string"
          ? payload.execution_status
          : record.execution_status,
      execution_performed: payload.execution_performed,
      rawdata_modified: payload.rawdata_modified,
    },
    warnings: record.warnings,
    next_actions: stringArray(payload.next_actions),
    source: "reviewed_plan",
    reviewed_plan_id: record.reviewed_plan_id,
    plan_hash: record.plan_hash,
    goal_contract_candidate: isRecord(payload.goal_contract_candidate)
      ? payload.goal_contract_candidate
      : undefined,
    goal_contract_status:
      typeof payload.goal_contract_status === "string"
        ? payload.goal_contract_status
        : record.status.toLowerCase() === "reviewed"
          ? "reviewed"
          : undefined,
  };
}

function isPlanOnlyDraft(draft: PresetPlanDraft | null | undefined): boolean {
  if (!draft) return false;
  const metadata = isRecord(draft.plan.metadata) ? draft.plan.metadata : {};
  const executionStatus = draft.validation?.execution_status;
  return (
    metadata.plan_only === true ||
    executionStatus === "NOT_EXECUTED_PLAN_ONLY" ||
    (draft.validation?.execution_performed === false && metadata.execution_enabled === false)
  );
}

function riskLabel(risk: NormalizedPlanNode["risk"], t: I18nContextValue["t"]): string {
  if (risk === "high") return t("plan.highRisk");
  if (risk === "approval") return t("plan.approval");
  if (risk === "unknown") return t("plan.unknown");
  return t("plan.cataloged");
}

function riskTone(
  risk: NormalizedPlanNode["risk"],
): "neutral" | "info" | "success" | "warning" | "danger" {
  if (risk === "high") return "danger";
  if (risk === "approval") return "warning";
  if (risk === "unknown") return "info";
  return "success";
}

function stepTone(state: StepState): "neutral" | "info" | "success" | "warning" {
  if (state === "completed") return "success";
  if (state === "current") return "info";
  if (state === "not-applicable") return "neutral";
  if (state === "attention" || state === "pending-evidence" || state === "locked") return "warning";
  return "neutral";
}

function stepLabel(state: StepState, t: I18nContextValue["t"]): string {
  if (state === "not-applicable") return t("plan.notApplicable");
  if (state === "pending-evidence") return t("plan.pendingEvidence");
  if (state === "completed") return t("common.completed");
  if (state === "current") return t("common.current");
  if (state === "locked") return t("common.blocked");
  if (state === "available") return t("common.available");
  return t("plan.needsReview");
}

function ReadOnlyReviewedPlan({ record }: { record: ReviewedPlanRecord }) {
  const { t } = useI18n();
  const metadata =
    isRecord(record.payload.plan) && isRecord(record.payload.plan.metadata)
      ? record.payload.plan.metadata
      : {};
  const readOnlyPayload = {
    reviewed_plan_id: record.reviewed_plan_id,
    plan_hash: record.plan_hash,
    status: record.status,
    capability_level: metadata.capability_level ?? "metadata_only",
    execution_status: record.payload.execution_status ?? record.execution_status,
    execution_performed: record.payload.execution_performed ?? false,
    rawdata_modified: record.payload.rawdata_modified ?? false,
    plan: record.payload.plan ?? {},
  };
  return (
    <Card className={styles.readOnlyPlan}>
      <div>
        <h3>{t("plan.readOnlyReviewedPlan")}</h3>
        <p>{t("plan.readOnlyReviewedPlanDescription")}</p>
      </div>
      <pre>{JSON.stringify(readOnlyPayload, null, 2)}</pre>
    </Card>
  );
}

function NodeInspector({ node }: { node: NormalizedPlanNode | null }) {
  const { t } = useI18n();
  if (!node) {
    return (
      <EmptyState title={t("plan.noNodeSelected")} description={t("plan.noNodeDescription")} />
    );
  }

  return (
    <div className={styles.nodeInspector}>
      <div className={styles.inspectorTitle}>
        <strong>{node.name}</strong>
        <Badge tone={riskTone(node.risk)} size="sm">
          {riskLabel(node.risk, t)}
        </Badge>
      </div>
      <p>{node.detail}</p>
      <InspectorSection title={t("plan.inputs")} emptyText={t("plan.inputsEmpty")}>
        {node.inputs.map((input) => (
          <li key={input}>{input}</li>
        ))}
      </InspectorSection>
      <InspectorSection title={t("plan.parameters")} emptyText={t("plan.parametersEmpty")}>
        {node.parameters.map((parameter) => (
          <li key={parameter.key}>
            <strong>{parameter.key}</strong>
            <span>{parameter.value}</span>
          </li>
        ))}
      </InspectorSection>
      <InspectorSection title={t("plan.risk")} emptyText="">
        <li>{node.riskReason}</li>
      </InspectorSection>
      <InspectorSection title={t("plan.outputs")} emptyText={t("plan.outputsEmpty")}>
        {node.outputs.map((output) => (
          <li key={output}>{output}</li>
        ))}
      </InspectorSection>
    </div>
  );
}

function InspectorSection({
  children,
  emptyText,
  title,
}: {
  children: ReactNode;
  emptyText: string;
  title: string;
}) {
  const items = Array.isArray(children) ? children.filter(Boolean) : children ? [children] : [];
  return (
    <section className={styles.inspectorSection}>
      <h4>{title}</h4>
      {items.length ? <ul>{items}</ul> : <p>{emptyText}</p>}
    </section>
  );
}
