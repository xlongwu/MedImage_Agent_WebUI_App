import { WorkspaceHeader } from "../dashboard/DashboardChrome";
import NiftiQcSnapshotPanel from "../../components/NiftiQcSnapshotPanel";
import BoldReferenceReadinessPanel from "../../components/BoldReferenceReadinessPanel";
import MotionQcReadinessPanel from "../../components/MotionQcReadinessPanel";
import { EvidenceBadge } from "../../components/domain/EvidenceBadge";
import { Badge, Button, Card, EmptyState, Table, TableEmpty } from "../../components/ui";
import { useQcEvidence, type QcOverviewEvidence } from "./useQcEvidence";
import type { EvidenceLevel } from "../../lib/evidence";
import type { MotionQcReadinessResponse, NativeFullPreprocResponse } from "../../types";
import styles from "./QCReportsWorkspace.module.css";
import layoutStyles from "./WorkspaceLayout.module.css";
import { useI18n } from "../../i18n/useI18n";
import type { I18nContextValue } from "../../i18n/context";

export interface QCReportsWorkspaceProps {
  baseUrl: string;
  projectId: string | null;
}

export function QCReportsWorkspace({ baseUrl, projectId }: QCReportsWorkspaceProps) {
  const { t } = useI18n();
  const hasProject = Boolean(projectId);

  return (
    <div className={layoutStyles.stack}>
      <WorkspaceHeader
        title={t("qc.title")}
        subtitle={t("qc.subtitle")}
        status={hasProject ? t("qc.review") : t("qc.selectProject")}
      />

      {!hasProject ? (
        <EmptyState title={t("qc.selectTitle")} description={t("qc.selectDescription")} />
      ) : (
        <QcDashboardOverview baseUrl={baseUrl} projectId={projectId!} />
      )}

      {hasProject ? (
        <section className={layoutStyles.panelGrid} aria-label={t("qc.detailedModules")}>
          <div id="nifti-qc-snapshot-panel">
            <NiftiQcSnapshotPanel baseUrl={baseUrl} projectId={projectId} />
          </div>
          <div id="bold-reference-readiness-panel">
            <BoldReferenceReadinessPanel baseUrl={baseUrl} projectId={projectId} />
          </div>
          <div id="motion-qc-readiness-panel">
            <MotionQcReadinessPanel baseUrl={baseUrl} projectId={projectId} />
          </div>
        </section>
      ) : null}
    </div>
  );
}

function QcDashboardOverview({ baseUrl, projectId }: { baseUrl: string; projectId: string }) {
  const { t } = useI18n();
  const evidence = useQcEvidence(baseUrl, projectId);

  const model = buildQcOverviewModel(evidence, t);

  return (
    <section className={styles.dashboardGrid} aria-label={t("qc.dashboardOverview")}>
      <Card className={styles.summaryCard} tone="muted">
        <div className={styles.cardHeader}>
          <div>
            <h3>{t("qc.evidenceDashboard")}</h3>
            <p>{model.summaryDescription}</p>
          </div>
          <EvidenceBadge level={model.evidenceLevel} />
        </div>
        {evidence.errorMessages.length > 0 ? (
          <div role="alert">
            <strong>{t("qc.evidenceLoadError")}</strong>
            <p>{evidence.errorMessages.join("; ")}</p>
            <Button
              disabled={evidence.loading}
              onClick={evidence.reload}
              size="sm"
              variant="secondary"
            >
              {t("common.retry")}
            </Button>
          </div>
        ) : null}
        <div className={styles.statusStrip} aria-label={t("qc.summaryStates")}>
          {model.evidenceStates.map((item) => (
            <div data-tone={item.tone} key={item.label}>
              <span>{item.label}</span>
              <strong>{item.value}</strong>
              <small>{item.description}</small>
            </div>
          ))}
        </div>
        <Table caption={t("qc.subjectStatus")}>
          <thead>
            <tr>
              <th>{t("qc.subject")}</th>
              <th>{t("qc.evidenceSource")}</th>
              <th>{t("qc.coverage")}</th>
              <th>{t("qc.warnings")}</th>
              <th>{t("qc.reviewState")}</th>
            </tr>
          </thead>
          <tbody>
            {model.subjectRows.length ? (
              model.subjectRows.map((row) => (
                <tr key={row.subjectId}>
                  <td>{row.subjectId}</td>
                  <td>{row.evidenceSource}</td>
                  <td>{row.coverage}</td>
                  <td>{row.warnings}</td>
                  <td>
                    <Badge tone={row.tone} size="sm">
                      {row.reviewState}
                    </Badge>
                  </td>
                </tr>
              ))
            ) : (
              <TableEmpty colSpan={5}>
                {evidence.loading ? t("qc.loadingEvidence") : t("qc.noSubjectRows")}
              </TableEmpty>
            )}
          </tbody>
        </Table>
      </Card>

      <Card className={styles.outlierCard}>
        <div className={styles.cardHeader}>
          <div>
            <h3>{t("qc.outlierFocus")}</h3>
            <p>{t("qc.outlierDescription")}</p>
          </div>
        </div>
        <ol className={styles.findingList} aria-label={t("qc.outlierAreas")}>
          {model.outlierAreas.map((item) => (
            <li key={item.label}>
              <div>
                <strong>{item.label}</strong>
                <p>{item.description}</p>
                <dl className={styles.evidenceMeta}>
                  <div>
                    <dt>{t("qc.source")}</dt>
                    <dd>{item.source}</dd>
                  </div>
                  <div>
                    <dt>{t("qc.unit")}</dt>
                    <dd>{item.unit}</dd>
                  </div>
                </dl>
              </div>
              <Badge tone={item.tone} size="sm">
                {item.status}
              </Badge>
            </li>
          ))}
        </ol>
        <details className={styles.drilldownShell}>
          <summary>{t("qc.drilldownContract")}</summary>
          <Table caption={t("qc.drilldownEvidence")}>
            <thead>
              <tr>
                <th>{t("qc.subjectRun")}</th>
                <th>{t("qc.metric")}</th>
                <th>{t("qc.threshold")}</th>
                <th>{t("qc.evidence")}</th>
              </tr>
            </thead>
            <tbody>
              {model.drilldownRows.length ? (
                model.drilldownRows.map((row) => (
                  <tr key={`${row.subjectRun}:${row.metric}:${row.evidence}`}>
                    <td>{row.subjectRun}</td>
                    <td>{row.metric}</td>
                    <td>{row.threshold}</td>
                    <td>{row.evidence}</td>
                  </tr>
                ))
              ) : (
                <TableEmpty colSpan={4}>{t("qc.drilldownEmpty")}</TableEmpty>
              )}
            </tbody>
          </Table>
        </details>
      </Card>

      <Card className={styles.comparisonCard}>
        <div className={styles.cardHeader}>
          <div>
            <h3>{t("qc.imageComparison")}</h3>
            <p>{model.comparison.description}</p>
          </div>
          <Badge tone={model.comparison.tone}>{model.comparison.status}</Badge>
        </div>
        <div className={styles.comparisonGate} aria-label={t("qc.comparisonGate")}>
          <strong>{model.comparison.title}</strong>
          <p>{model.comparison.body}</p>
          <ul>
            {comparisonRequirements(t).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
        <div className={styles.comparisonStates} aria-label={t("qc.comparisonStates")}>
          {comparisonStates(t).map((item) => (
            <div key={item.label} data-state={item.state}>
              <span>{item.label}</span>
              <strong>{item.status}</strong>
              <small>{item.description}</small>
            </div>
          ))}
        </div>
        <p className={styles.helperText}>{t("qc.comparisonHelp")}</p>
      </Card>

      <Card className={styles.metricsCard}>
        <div className={styles.cardHeader}>
          <div>
            <h3>{t("qc.chartContract")}</h3>
            <p>{t("qc.chartContractDescription")}</p>
          </div>
        </div>
        <dl className={styles.chartContractList}>
          {model.chartContracts.map((item) => (
            <div key={item.label}>
              <dt>
                {item.label}
                <Badge tone={item.tone} size="sm">
                  {item.status}
                </Badge>
              </dt>
              <dd>
                <span>{t("qc.unitValue", { value: item.unit })}</span>
                <span>{t("qc.thresholdValue", { value: item.threshold })}</span>
                <span>{t("qc.range", { value: item.range })}</span>
                <span>{t("qc.sourceValue", { value: item.source })}</span>
              </dd>
            </div>
          ))}
        </dl>
      </Card>

      <Card className={styles.visualSpecCard}>
        <div className={styles.cardHeader}>
          <div>
            <h3>{t("qc.visualizationContract")}</h3>
            <p>{t("qc.visualizationDescription")}</p>
          </div>
          <Badge tone="info">{t("qc.required")}</Badge>
        </div>
        <dl className={styles.visualSpecList} aria-label={t("qc.visualizationRequirements")}>
          {visualizationRequirements(t).map((item) => (
            <div key={item.label}>
              <dt>{item.label}</dt>
              <dd>{item.description}</dd>
            </div>
          ))}
        </dl>
      </Card>
    </section>
  );
}

type QcSubjectRow = {
  coverage: string;
  evidenceSource: string;
  reviewState: string;
  subjectId: string;
  tone: BadgeTone;
  warnings: number;
};

type QcOverviewModel = {
  chartContracts: QcChartContract[];
  comparison: {
    body: string;
    description: string;
    status: string;
    title: string;
    tone: BadgeTone;
  };
  drilldownRows: {
    evidence: string;
    metric: string;
    subjectRun: string;
    threshold: string;
  }[];
  evidenceLevel: EvidenceLevel;
  evidenceStates: QcEvidenceState[];
  outlierAreas: QcOutlierArea[];
  subjectRows: QcSubjectRow[];
  summaryDescription: string;
};

function buildQcOverviewModel(
  evidence: QcOverviewEvidence,
  t: I18nContextValue["t"],
): QcOverviewModel {
  const sources = collectEvidenceSources(evidence, t);
  const subjectRows = buildSubjectRows(evidence, t);
  const nativeComputed = hasNativeComputedEvidence(evidence.nativeRun);
  const hasEvidence = sources.length > 0;
  const warningCount = evidence.nativeRun
    ? evidence.nativeRun.warning_stages.length + subjectReadinessWarningCount(evidence)
    : (evidence.qcReport?.warning_count ??
      (evidence.niftiSnapshot?.warning_count ?? 0) +
        (evidence.boldReadiness?.warning_count ?? 0) +
        (evidence.motionReadiness?.warnings.length ?? 0));
  const blockedCount = evidence.nativeRun
    ? evidence.nativeRun.blocked_stages.length + evidence.nativeRun.failed_stages.length
    : (evidence.qcReport?.blocked_count ??
      (evidence.boldReadiness?.blocked_count ?? 0) +
        (evidence.motionReadiness?.status === "blocked" ? 1 : 0));
  const evidenceLevel: EvidenceLevel = nativeComputed
    ? "computed"
    : hasEvidence
      ? "created"
      : "backend_required";

  return {
    chartContracts: buildChartContracts(evidence, t),
    comparison: buildComparisonModel(evidence, t),
    drilldownRows: buildDrilldownRows(evidence, t),
    evidenceLevel,
    evidenceStates: [
      {
        label: t("qc.model.evidenceLabel"),
        value: hasEvidence ? t("qc.model.evidenceLoaded") : t("plan.backendRequired"),
        description: hasEvidence ? sources.join(", ") : t("qc.model.awaitingReports"),
        tone: hasEvidence ? "success" : "neutral",
      },
      {
        label: t("qc.model.coverageLabel"),
        value: subjectRows.length
          ? t("qc.model.subjectCount", { count: subjectRows.length })
          : t("plan.backendRequired"),
        description: subjectRows.length
          ? t("qc.model.coverageLoaded")
          : t("qc.model.coveragePending"),
        tone: subjectRows.length ? "success" : "info",
      },
      {
        label: t("qc.model.warningsLabel"),
        value: String(warningCount),
        description: hasEvidence
          ? t("qc.model.blockedCount", { count: blockedCount })
          : t("qc.model.warningsNotInferred"),
        tone: warningCount > 0 ? "warning" : hasEvidence ? "success" : "warning",
      },
      {
        label: t("qc.model.decisionLabel"),
        value: evidence.nativeRun?.status
          ? t("qc.model.nativeStatus", { status: evidence.nativeRun.status })
          : evidence.qcReport?.status
            ? t("qc.model.reportStatus", { status: evidence.qcReport.status })
            : t("plan.backendRequired"),
        description: hasEvidence ? t("qc.model.decisionLoaded") : t("qc.model.decisionPending"),
        tone: blockedCount > 0 ? "warning" : hasEvidence ? "success" : "info",
      },
    ],
    outlierAreas: buildOutlierAreas(evidence, t),
    subjectRows,
    summaryDescription: hasEvidence
      ? t("qc.model.summaryLoaded")
      : evidence.loading
        ? t("qc.model.summaryLoading")
        : t("qc.model.summaryPending"),
  };
}

function buildDrilldownRows(
  evidence: QcOverviewEvidence,
  t: I18nContextValue["t"],
): QcOverviewModel["drilldownRows"] {
  const rows: QcOverviewModel["drilldownRows"] = [];
  for (const candidate of evidence.motionReadiness?.candidates ?? []) {
    const subjectId = normalizeSubjectId(candidate.subject_id, candidate.bold_path);
    if (!subjectId) continue;
    rows.push({
      subjectRun: candidate.session_id ? `${subjectId}/${candidate.session_id}` : subjectId,
      metric: candidate.has_fd_column ? "FD / DVARS" : t("qc.model.motionOutliers"),
      threshold: candidate.has_fd_column
        ? t("qc.model.backendSupplied")
        : t("qc.model.pendingMetadata"),
      evidence:
        candidate.fd_source_path ??
        candidate.motion_param_paths[0] ??
        candidate.relative_path ??
        candidate.bold_path,
    });
  }
  for (const stage of evidence.nativeRun?.stage_results ?? []) {
    if (
      !["alff", "falff", "functional_connectivity", "motion_qc", "reho"].includes(stage.stage_id)
    ) {
      continue;
    }
    const subjectId = nativeStageSubjectId(stage) ?? evidence.nativeRun?.run_id ?? "—";
    for (const artifact of stage.output_artifacts) {
      const artifactPath = typeof artifact.path === "string" ? artifact.path : "";
      if (!artifactPath) continue;
      rows.push({
        subjectRun: subjectId,
        metric: stage.display_name || stage.stage_id,
        threshold: stage.validation_status || t("qc.model.backendSupplied"),
        evidence: artifactPath,
      });
    }
  }
  return rows;
}

function collectEvidenceSources(evidence: QcOverviewEvidence, t: I18nContextValue["t"]): string[] {
  const sources: string[] = [];
  if (evidence.qcReport) sources.push(t("qc.model.sourceDashboard"));
  if ((evidence.niftiSnapshot?.image_count ?? 0) > 0) sources.push(t("qc.model.sourceNifti"));
  if ((evidence.boldReadiness?.candidate_count ?? 0) > 0) sources.push(t("qc.model.sourceBold"));
  if ((evidence.motionReadiness?.candidate_count ?? 0) > 0)
    sources.push(t("qc.model.sourceMotion"));
  if (evidence.nativeRun?.stage_results.length) sources.push(t("qc.model.sourceNative"));
  return sources;
}

function buildSubjectRows(evidence: QcOverviewEvidence, t: I18nContextValue["t"]): QcSubjectRow[] {
  const rows = new Map<
    string,
    {
      coverage: Set<string>;
      sources: Set<string>;
      warnings: Set<string>;
    }
  >();
  const ensure = (subjectId?: string | null, path?: string | null) => {
    subjectId = normalizeSubjectId(subjectId, path);
    if (!subjectId) return null;
    if (!rows.has(subjectId)) {
      rows.set(subjectId, { coverage: new Set(), sources: new Set(), warnings: new Set() });
    }
    return rows.get(subjectId)!;
  };

  for (const image of evidence.niftiSnapshot?.images ?? []) {
    const row = ensure(image.subject_id, image.path);
    if (!row) continue;
    row.sources.add(t("qc.model.nifti"));
    row.coverage.add(
      image.modality === "bold" || image.suffix === "bold"
        ? t("qc.model.boldImage")
        : t("qc.model.niftiImage"),
    );
    image.warnings.forEach((warning) => row.warnings.add(`nifti:${warning}`));
  }
  for (const candidate of evidence.boldReadiness?.candidates ?? []) {
    const row = ensure(candidate.subject_id, candidate.bold_path);
    if (!row) continue;
    row.sources.add(t("qc.model.sourceBold"));
    row.coverage.add(candidate.is_4d ? t("qc.model.fourDBold") : t("qc.model.boldCandidate"));
    candidate.warnings.forEach((warning) => row.warnings.add(`bold:${warning}`));
  }
  for (const candidate of evidence.motionReadiness?.candidates ?? []) {
    const row = ensure(candidate.subject_id, candidate.bold_path);
    if (!row) continue;
    row.sources.add(t("qc.model.sourceMotion"));
    row.coverage.add(
      candidate.has_fd_column ? t("qc.model.fdAvailable") : t("qc.model.motionPending"),
    );
    candidate.warnings.forEach((warning) => row.warnings.add(`motion:${warning}`));
  }
  for (const stage of evidence.nativeRun?.stage_results ?? []) {
    const subjectId = nativeStageSubjectId(stage);
    const artifactPath = stage.output_artifacts[0]?.path;
    const row = ensure(subjectId, typeof artifactPath === "string" ? artifactPath : null);
    if (!row) continue;
    row.sources.add(t("qc.model.nativePreprocessing"));
    if (stage.stage_id === "motion_qc" && nativeStageProduced(stage))
      row.coverage.add(t("qc.model.motionQc"));
    if (stage.stage_id === "normalization" && nativeStageProduced(stage))
      row.coverage.add(t("qc.model.normalizedBold"));
    if (stage.stage_id === "functional_connectivity" && nativeStageProduced(stage))
      row.coverage.add(t("qc.model.fcMatrix"));
    if (stage.status === "warning" || stage.status === "simplified") {
      row.warnings.add(`native:${stage.stage_id}`);
    }
  }

  return Array.from(rows.entries())
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([subjectId, row]) => ({
      coverage: Array.from(row.coverage).join(", "),
      evidenceSource: Array.from(row.sources).join(", "),
      reviewState: row.warnings.size > 0 ? t("qc.review") : t("qc.model.ready"),
      subjectId,
      tone: row.warnings.size > 0 ? "warning" : "success",
      warnings: row.warnings.size,
    }));
}

function buildOutlierAreas(
  evidence: QcOverviewEvidence,
  t: I18nContextValue["t"],
): QcOutlierArea[] {
  const nativeMotionSubjects = nativeStageSubjectCount(evidence.nativeRun, ["motion_qc"]);
  const readinessMotionSubjects = uniqueReadinessSubjectCount(evidence.motionReadiness, true);
  const motionSubjectCount = nativeMotionSubjects || readinessMotionSubjects;
  const motionReady = motionSubjectCount > 0;
  const nativeSpatialArtifacts = spatialNativeArtifactCount(evidence.nativeRun);
  const spatialReady =
    nativeSpatialArtifacts > 0 ||
    ((evidence.boldReadiness?.ready_count ?? 0) > 0 &&
      (evidence.niftiSnapshot?.four_d_count ?? 0) > 0);
  const reportCreated = Boolean(evidence.qcReport || evidence.nativeRun?.final_report_path);

  return [
    {
      label: t("qc.model.motionOutliers"),
      description: t("qc.model.motionDescription"),
      source: motionReady ? t("qc.model.motionEvidence") : t("qc.model.motionQcEvidence"),
      status: motionReady
        ? t("qc.model.fdReady", { count: motionSubjectCount })
        : t("qc.model.awaitingMetrics"),
      tone: motionReady ? "success" : "neutral",
      unit: t("qc.model.motionUnit"),
    },
    {
      label: t("qc.model.spatialAlignment"),
      description: t("qc.model.spatialDescription"),
      source:
        nativeSpatialArtifacts > 0
          ? t("qc.model.nativeSpatial")
          : spatialReady
            ? t("qc.model.boldNiftiArtifacts")
            : t("qc.model.snapshotArtifacts"),
      status:
        nativeSpatialArtifacts > 0
          ? t("qc.model.partialArtifact")
          : spatialReady
            ? t("qc.model.readyInputs")
            : t("qc.model.artifactGated"),
      tone: spatialReady ? "success" : "warning",
      unit: t("qc.model.spatialUnit"),
    },
    {
      label: t("qc.model.reportCompleteness"),
      description: t("qc.model.reportDescription"),
      source: reportCreated ? t("qc.model.nativeQcReport") : t("qc.model.qcPlanningReport"),
      status: reportCreated ? t("qc.model.created") : t("qc.review"),
      tone: reportCreated ? "success" : "info",
      unit: t("qc.model.checklist"),
    },
  ];
}

function buildComparisonModel(
  evidence: QcOverviewEvidence,
  t: I18nContextValue["t"],
): QcOverviewModel["comparison"] {
  const comparisonSubjects = nativeComparisonSubjectCount(evidence.nativeRun);
  if (comparisonSubjects > 0) {
    return {
      body: t("qc.model.comparisonReadyBody", { count: comparisonSubjects }),
      description: t("qc.model.comparisonReadyDescription"),
      status: t("qc.model.readyArtifact"),
      title: t("qc.model.comparisonReadyTitle"),
      tone: "success",
    };
  }
  const artifactCount = spatialNativeArtifactCount(evidence.nativeRun);
  if (artifactCount > 0) {
    return {
      body: t("qc.model.comparisonPartialBody", { count: artifactCount }),
      description: t("qc.model.comparisonPartialDescription"),
      status: t("qc.model.partialArtifact"),
      title: t("qc.model.comparisonPartialTitle"),
      tone: "warning",
    };
  }
  return {
    body: t("qc.model.comparisonEmptyBody"),
    description: t("qc.model.comparisonEmptyDescription"),
    status: t("qc.model.noArtifact"),
    title: t("qc.model.comparisonEmptyTitle"),
    tone: "warning",
  };
}

function buildChartContracts(
  evidence: QcOverviewEvidence,
  t: I18nContextValue["t"],
): QcChartContract[] {
  const fcStages = findNativeStages(evidence.nativeRun, "functional_connectivity");
  const motionStages = findNativeStages(evidence.nativeRun, "motion_qc");
  const fcComputed = fcStages.some(nativeStageProduced);
  const motionSubjectCount =
    nativeStageSubjectCount(evidence.nativeRun, ["motion_qc"]) ||
    uniqueReadinessSubjectCount(evidence.motionReadiness, true);
  const motionReady = motionSubjectCount > 0;
  const nativeSpatialArtifacts = spatialNativeArtifactCount(evidence.nativeRun);
  const spatialReady =
    nativeSpatialArtifacts > 0 ||
    ((evidence.boldReadiness?.ready_count ?? 0) > 0 &&
      (evidence.niftiSnapshot?.four_d_count ?? 0) > 0);

  return [
    {
      label: "FD / DVARS",
      range: motionReady
        ? t("qc.model.subjectCount", { count: motionSubjectCount })
        : t("qc.model.pendingSubjects"),
      source: motionStages.length
        ? t("qc.model.nativeMotionArtifact")
        : t("qc.model.motionMetricsArtifact"),
      status:
        motionReady || motionStages.length ? t("qc.model.created") : t("plan.backendRequired"),
      threshold: motionReady ? t("qc.model.fdBackend") : t("qc.model.pendingMetadata"),
      tone: motionReady || motionStages.length ? "success" : "warning",
      unit: t("qc.model.motionUnit"),
    },
    {
      label: t("qc.model.spatialAlignment"),
      range:
        nativeSpatialArtifacts > 0
          ? t("qc.model.spatialArtifactCount", { count: nativeSpatialArtifacts })
          : spatialReady
            ? t("qc.model.boldCandidateCount", { count: evidence.boldReadiness!.ready_count })
            : t("qc.model.pendingSnapshots"),
      source:
        nativeSpatialArtifacts > 0 ? t("qc.model.nativeSpatial") : t("qc.model.boldT1Artifacts"),
      status: spatialReady ? t("qc.model.created") : t("plan.backendRequired"),
      threshold: t("qc.model.backendSupplied"),
      tone: spatialReady ? "success" : "info",
      unit: t("qc.model.spatialUnit"),
    },
    {
      label: "ALFF / fALFF",
      range: nativeStageRange(evidence.nativeRun, ["alff", "falff"], t),
      source: t("qc.model.derivedModules"),
      status: nativeAnyProduced(evidence.nativeRun, ["alff", "falff"])
        ? nativeAnyWarning(evidence.nativeRun, ["alff", "falff"])
          ? t("qc.model.computedWarnings")
          : t("preprocessing.flow.backendComputed")
        : t("common.unavailable"),
      threshold: nativeAnyProduced(evidence.nativeRun, ["alff", "falff"])
        ? t("qc.model.warningsDisclosed")
        : t("qc.model.notApplicable"),
      tone: nativeAnyWarning(evidence.nativeRun, ["alff", "falff"])
        ? "warning"
        : nativeAnyProduced(evidence.nativeRun, ["alff", "falff"])
          ? "success"
          : "neutral",
      unit: t("qc.model.backendDefined"),
    },
    {
      label: t("qc.model.rehoFc"),
      range: fcComputed
        ? t("qc.model.fcRange", {
            subjects: nativeStageSubjectCount(evidence.nativeRun, ["functional_connectivity"]),
            artifacts: fcStages.reduce((total, stage) => total + stage.output_artifacts.length, 0),
          })
        : nativeStageRange(evidence.nativeRun, ["reho", "functional_connectivity"], t),
      source: t("qc.model.derivedModules"),
      status: fcComputed ? t("qc.model.fcComputed") : t("common.unavailable"),
      threshold: fcComputed ? t("qc.model.atlasEvidence") : t("qc.model.notApplicable"),
      tone: fcComputed ? "success" : "neutral",
      unit: t("qc.model.backendDefined"),
    },
  ];
}

function hasNativeComputedEvidence(nativeRun: NativeFullPreprocResponse | null): boolean {
  return Boolean(
    nativeRun?.stage_results.some(
      (stage) =>
        (stage.status === "succeeded" || stage.status === "simplified") &&
        (stage.capability_level === "computed" || stage.output_artifacts.length > 0),
    ),
  );
}

function normalizeSubjectId(subjectId?: string | null, path?: string | null): string | null {
  for (const value of [subjectId, path]) {
    const match = value?.match(/(?:^|[/\\])?(sub-[A-Za-z0-9]+)(?=[_/\\.]|$)/);
    if (match) return match[1];
  }
  return null;
}

function spatialNativeArtifactCount(nativeRun: NativeFullPreprocResponse | null): number {
  const spatialTokens = [
    "align",
    "atlas",
    "coreg",
    "mean_functional",
    "normalization",
    "realign",
    "reference",
    "registration",
    "spatial",
    "transform",
  ];
  return (nativeRun?.stage_results ?? []).reduce((total, stage) => {
    const stageText = `${stage.stage_id} ${stage.display_name ?? ""}`.toLowerCase();
    if (!spatialTokens.some((token) => stageText.includes(token))) return total;
    return total + stage.output_artifacts.length;
  }, 0);
}

function findNativeStages(nativeRun: NativeFullPreprocResponse | null, stageId: string) {
  return nativeRun?.stage_results.filter((stage) => stage.stage_id === stageId) ?? [];
}

function nativeStageProduced(stage: NativeFullPreprocResponse["stage_results"][number]): boolean {
  return (
    ["succeeded", "simplified", "warning"].includes(stage.status) &&
    stage.output_artifacts.length > 0
  );
}

function nativeAnyProduced(
  nativeRun: NativeFullPreprocResponse | null,
  stageIds: string[],
): boolean {
  return stageIds.some((stageId) => findNativeStages(nativeRun, stageId).some(nativeStageProduced));
}

function nativeAnyWarning(
  nativeRun: NativeFullPreprocResponse | null,
  stageIds: string[],
): boolean {
  return stageIds.some((stageId) =>
    findNativeStages(nativeRun, stageId).some((stage) => stage.status === "warning"),
  );
}

function nativeStageRange(
  nativeRun: NativeFullPreprocResponse | null,
  stageIds: string[],
  t: I18nContextValue["t"],
): string {
  const stages = stageIds.flatMap((stageId) => findNativeStages(nativeRun, stageId));
  if (!stages.length) return t("qc.model.pendingArtifacts");
  const produced = stages.filter(nativeStageProduced);
  const skipped = stages.filter((stage) => stage.status === "skipped").length;
  if (produced.length) {
    const subjects = new Set(produced.map(nativeStageSubjectId).filter(Boolean)).size;
    const artifacts = produced.reduce((total, stage) => total + stage.output_artifacts.length, 0);
    return subjects
      ? t("qc.model.artifactRange", { subjects, artifacts })
      : t("qc.model.computedArtifactCount", { count: artifacts });
  }
  if (skipped) return t("qc.model.skippedStageCount", { count: skipped });
  return t("qc.model.pendingArtifacts");
}

function nativeStageSubjectId(
  stage: NativeFullPreprocResponse["stage_results"][number],
): string | null {
  const resultSubject = stage.result?.subject_id;
  const artifactPath = stage.output_artifacts[0]?.path;
  return normalizeSubjectId(
    typeof resultSubject === "string" ? resultSubject : null,
    typeof artifactPath === "string" ? artifactPath : null,
  );
}

function nativeStageSubjectCount(
  nativeRun: NativeFullPreprocResponse | null,
  stageIds: string[],
): number {
  return new Set(
    stageIds
      .flatMap((stageId) => findNativeStages(nativeRun, stageId))
      .filter(nativeStageProduced)
      .map(nativeStageSubjectId)
      .filter((subjectId): subjectId is string => Boolean(subjectId)),
  ).size;
}

function nativeComparisonSubjectCount(nativeRun: NativeFullPreprocResponse | null): number {
  const references = new Set(
    findNativeStages(nativeRun, "realignment")
      .filter(nativeStageProduced)
      .map(nativeStageSubjectId)
      .filter((subjectId): subjectId is string => Boolean(subjectId)),
  );
  const processed = new Set(
    findNativeStages(nativeRun, "normalization")
      .filter(nativeStageProduced)
      .map(nativeStageSubjectId)
      .filter((subjectId): subjectId is string => Boolean(subjectId)),
  );
  return Array.from(references).filter((subjectId) => processed.has(subjectId)).length;
}

function uniqueReadinessSubjectCount(
  readiness: MotionQcReadinessResponse | null,
  requireFd: boolean,
): number {
  return new Set(
    (readiness?.candidates ?? [])
      .filter((candidate) => !requireFd || candidate.has_fd_column)
      .map((candidate) => normalizeSubjectId(candidate.subject_id, candidate.bold_path))
      .filter((subjectId): subjectId is string => Boolean(subjectId)),
  ).size;
}

function subjectReadinessWarningCount(evidence: QcOverviewEvidence): number {
  const warnings = new Set<string>();
  for (const candidate of evidence.boldReadiness?.candidates ?? []) {
    const subjectId = normalizeSubjectId(candidate.subject_id, candidate.bold_path);
    candidate.warnings.forEach((warning) =>
      warnings.add(`${subjectId ?? "unknown"}:bold:${warning}`),
    );
  }
  for (const candidate of evidence.motionReadiness?.candidates ?? []) {
    const subjectId = normalizeSubjectId(candidate.subject_id, candidate.bold_path);
    candidate.warnings.forEach((warning) =>
      warnings.add(`${subjectId ?? "unknown"}:motion:${warning}`),
    );
  }
  return warnings.size;
}

type BadgeTone = "neutral" | "info" | "success" | "warning" | "danger";

type QcEvidenceState = {
  description: string;
  label: string;
  tone: BadgeTone;
  value: string;
};

type QcOutlierArea = {
  description: string;
  label: string;
  source: string;
  status: string;
  tone: BadgeTone;
  unit: string;
};

function comparisonRequirements(t: I18nContextValue["t"]): string[] {
  return [
    t("qc.model.referenceArtifact"),
    t("qc.model.processedArtifact"),
    t("qc.model.transformEvidence"),
    t("qc.model.comparableMetadata"),
  ];
}

function comparisonStates(t: I18nContextValue["t"]): Array<{
  description: string;
  label: string;
  state: "blocked" | "partial" | "ready";
  status: string;
}> {
  return [
    {
      label: t("qc.model.noArtifact"),
      state: "blocked",
      status: t("plan.backendRequired"),
      description: t("qc.model.noArtifactDescription"),
    },
    {
      label: t("qc.model.partialArtifact"),
      state: "partial",
      status: t("preprocessing.flow.metadataOnly"),
      description: t("qc.model.partialArtifactDescription"),
    },
    {
      label: t("qc.model.readyArtifact"),
      state: "ready",
      status: t("qc.model.created"),
      description: t("qc.model.readyArtifactDescription"),
    },
  ];
}

type QcChartContract = {
  label: string;
  range: string;
  source: string;
  status: string;
  threshold: string;
  tone: BadgeTone;
  unit: string;
};

function visualizationRequirements(t: I18nContextValue["t"]) {
  return [
    { label: t("qc.unit"), description: t("qc.model.unitRequirement") },
    { label: t("qc.threshold"), description: t("qc.model.thresholdRequirement") },
    { label: t("qc.model.dataRange"), description: t("qc.model.rangeRequirement") },
    { label: t("qc.model.drilldown"), description: t("qc.model.drilldownRequirement") },
  ];
}
