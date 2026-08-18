import { WorkspaceHeader } from "../dashboard/DashboardChrome";
import { Badge, Button, Card, EmptyState } from "../../components/ui";
import type { ProjectDataState, ProjectInventory } from "../../lib/projectWorkflow";
import styles from "./PreprocessingWorkspace.module.css";
import layoutStyles from "./WorkspaceLayout.module.css";
import { useI18n } from "../../i18n/useI18n";
import type { I18nContextValue } from "../../i18n/context";
import type { MessageKey } from "../../i18n/messages/en";

export interface PreprocessingWorkspaceProps {
  baseUrl: string;
  projectId: string | null;
  dataState?: ProjectDataState;
  inventory: ProjectInventory | null;
  hasPreprocessingRun: boolean;
  preprocessingRunId?: string | null;
  onOpenDataConversion: () => void;
  onOpenToolsDrawer: () => void;
  onOpenAgent?: () => void;
}

export function PreprocessingWorkspace({
  baseUrl: _baseUrl,
  projectId,
  dataState,
  inventory,
  hasPreprocessingRun,
  preprocessingRunId,
  onOpenDataConversion,
  onOpenToolsDrawer: _onOpenToolsDrawer,
  onOpenAgent,
}: PreprocessingWorkspaceProps) {
  const { t } = useI18n();
  const [selectedStageName, setSelectedStageName] = useState(preprocessingStages[0].id);
  const [configMode, setConfigMode] = useState<ConfigMode>("basic");
  const resolvedInventory = inventory ?? emptyProjectInventory(dataState, t);
  const isRawDicom = dataState === "raw_dicom";
  const hasRegisteredConvertedInput =
    resolvedInventory.hasConvertedData &&
    !resolvedInventory.metadataOnlyNiftiInventory &&
    (resolvedInventory.convertedSubjects > 0 || resolvedInventory.niftiFileCount > 0);
  const effectiveHasPreprocessingRun = hasPreprocessingRun || Boolean(preprocessingRunId);

  if (isRawDicom) {
    return (
      <div className={layoutStyles.stack}>
        <WorkspaceHeader
          title={t("preprocessing.title")}
          subtitle={t("preprocessing.validationSubtitle")}
          status={t("common.blocked")}
        />
        <section className={layoutStyles.blockedNotice} aria-label={t("preprocessing.blockedAria")}>
          <div className={layoutStyles.blockedBody}>
            <h3>{t("preprocessing.blocked")}</h3>
            <p>{t("preprocessing.blockedDescription")}</p>
            <ol
              className={layoutStyles.dependencyChain}
              aria-label={t("preprocessing.dependencyChain")}
            >
              <li className={layoutStyles.dependencyDone}>
                <span className={layoutStyles.dependencyLabel}>
                  {t("preprocessing.dataDetection")}
                </span>
                <span className={layoutStyles.dependencyStatus}>{t("preprocessing.done")}</span>
              </li>
              <li className={layoutStyles.dependencyCurrent}>
                <span className={layoutStyles.dependencyLabel}>
                  {t("preprocessing.conversionReview")}
                </span>
                <span className={layoutStyles.dependencyStatus}>{t("preprocessing.required")}</span>
              </li>
              <li>
                <span className={layoutStyles.dependencyLabel}>
                  {t("preprocessing.bidsValidation")}
                </span>
                <span className={layoutStyles.dependencyStatus}>{t("preprocessing.pending")}</span>
              </li>
              <li>
                <span className={layoutStyles.dependencyLabel}>{t("preprocessing.title")}</span>
                <span className={layoutStyles.dependencyStatus}>{t("preprocessing.locked")}</span>
              </li>
            </ol>
          </div>
          <div className={layoutStyles.blockedActions}>
            <Button variant="primary" onClick={onOpenDataConversion}>
              {t("preprocessing.returnData")}
            </Button>
            <span className={layoutStyles.blockedHint}>{t("preprocessing.rawReadOnly")}</span>
          </div>
        </section>
      </div>
    );
  }

  const isMissingRegistration =
    dataState === "empty" || dataState === "unknown" || !hasRegisteredConvertedInput;

  if (isMissingRegistration) {
    return (
      <div className={layoutStyles.stack}>
        <WorkspaceHeader
          title={t("preprocessing.title")}
          subtitle={t("preprocessing.registerSubtitle")}
          status={t("preprocessing.inputRequired")}
        />
        <section
          className={styles.inputRequiredGrid}
          aria-label={t("preprocessing.inputRequiredAria")}
        >
          <Card className={styles.inputRequiredCard} tone="muted">
            <div className={styles.sectionHeader}>
              <div>
                <h3>{t("preprocessing.registerTitle")}</h3>
                <p>{t("preprocessing.registerDescription")}</p>
              </div>
              <Badge tone="warning">{t("preprocessing.inputRequired")}</Badge>
            </div>
            <ol
              className={styles.requirementList}
              aria-label={t("preprocessing.inputRequirements")}
            >
              <li data-state="complete">
                <span>{t("preprocessing.projectContext")}</span>
                <strong>
                  {projectId ? t("preprocessing.selected") : t("preprocessing.missing")}
                </strong>
              </li>
              <li data-state={resolvedInventory.hasConvertedData ? "complete" : "blocked"}>
                <span>{t("preprocessing.convertedEvidence")}</span>
                <strong>
                  {resolvedInventory.hasConvertedData
                    ? t("preprocessing.detected")
                    : t("preprocessing.notRegistered")}
                </strong>
              </li>
              <li data-state={hasRegisteredConvertedInput ? "complete" : "blocked"}>
                <span>{t("preprocessing.registeredInput")}</span>
                <strong>
                  {hasRegisteredConvertedInput
                    ? t("preprocessing.ready")
                    : t("preprocessing.required")}
                </strong>
              </li>
            </ol>
            <div className={styles.inputRequiredActions}>
              <Button variant="primary" onClick={onOpenDataConversion}>
                {t("preprocessing.openData")}
              </Button>
              <span>{t("preprocessing.configLocked")}</span>
            </div>
          </Card>
          <InputReadinessCard inventory={resolvedInventory} />
        </section>
      </div>
    );
  }

  return (
    <div className={layoutStyles.stack}>
      <WorkspaceHeader
        title={t("preprocessing.title")}
        subtitle={t("preprocessing.subtitle")}
        status={
          isMissingRegistration
            ? t("preprocessing.inputRequired")
            : effectiveHasPreprocessingRun
              ? t("preprocessing.runAvailable")
              : t("preprocessing.readyConfigure")
        }
      />
      {!effectiveHasPreprocessingRun && (
        <EmptyState
          className={styles.setupCallout}
          title={t("preprocessing.createTitle")}
          description={t("preprocessing.createDescription")}
          action={
            onOpenAgent ? (
              <Button variant="primary" onClick={onOpenAgent}>
                {t("nav.agent")}
              </Button>
            ) : undefined
          }
        />
      )}
      <PreprocessingStageOverview
        configMode={configMode}
        hasPreprocessingRun={effectiveHasPreprocessingRun}
        inventory={resolvedInventory}
        isMissingRegistration={isMissingRegistration}
        onConfigModeChange={setConfigMode}
        onSelectStage={setSelectedStageName}
        selectedStageName={selectedStageName}
      />
    </div>
  );
}

type StageStatus = "registered" | "configure" | "waiting" | "review" | "locked";
type ConfigMode = "basic" | "advanced";

type ConfigParameter = {
  label: string;
  note: string;
  range?: string;
  unit?: string;
  value: string;
};

type PreprocessingStageDefinition = {
  id: string;
  nameKey: MessageKey;
  descriptionKey: MessageKey;
  basic: ConfigParameter[];
  advanced: ConfigParameter[];
};

const preprocessingStages: PreprocessingStageDefinition[] = [
  {
    id: "data-preparation",
    nameKey: "preprocessing.stageDataPreparation",
    descriptionKey: "preprocessing.stageDataPreparationDescription",
    basic: [
      { label: "Input dataset", value: "Registered BIDS/NIfTI", note: "Required before planning." },
      {
        label: "Subject scope",
        value: "All registered subjects",
        note: "Review exclusions later.",
      },
    ],
    advanced: [
      { label: "BIDS filter", value: "func/*bold", note: "Default functional input pattern." },
      { label: "Derivative root", value: "project derivatives", note: "Must stay under project." },
    ],
  },
  {
    id: "slice-timing",
    nameKey: "preprocessing.stageSliceTiming",
    descriptionKey: "preprocessing.stageSliceTimingDescription",
    basic: [
      {
        label: "TR",
        value: "from sidecar",
        unit: "s",
        note: "Loaded from BIDS JSON when present.",
      },
      { label: "Reference slice", value: "middle", note: "Common default for review." },
    ],
    advanced: [
      { label: "Slice order", value: "sidecar timing", note: "Fallback requires manual review." },
      { label: "Acquisition timing", value: "BIDS SliceTiming", note: "No inference in UI." },
    ],
  },
  {
    id: "motion-correction",
    nameKey: "preprocessing.stageMotionCorrection",
    descriptionKey: "preprocessing.stageMotionCorrectionDescription",
    basic: [
      { label: "Realign", value: "enabled", note: "Dry-run before execution." },
      { label: "FD threshold", value: "0.5", unit: "mm", range: "0.2-1.0", note: "QC flag only." },
    ],
    advanced: [
      { label: "Interpolation", value: "4th degree B-spline", note: "SPM-style parameter." },
      { label: "Quality", value: "0.9", range: "0-1", note: "Registration quality setting." },
    ],
  },
  {
    id: "coregistration",
    nameKey: "preprocessing.stageCoregistration",
    descriptionKey: "preprocessing.stageCoregistrationDescription",
    basic: [
      { label: "Alignment", value: "BOLD to T1w", note: "Requires anatomical input." },
      { label: "Preview", value: "QC overlay", note: "Review before downstream use." },
    ],
    advanced: [
      { label: "Cost function", value: "nmi", note: "Normalized mutual information." },
      { label: "Sampling", value: "4", unit: "mm", note: "SPM-style separation." },
    ],
  },
  {
    id: "segmentation",
    nameKey: "preprocessing.stageSegmentation",
    descriptionKey: "preprocessing.stageSegmentationDescription",
    basic: [
      { label: "Tissue classes", value: "GM / WM / CSF", note: "Used by nuisance model." },
      { label: "T1w source", value: "registered anatomical", note: "Required for segmentation." },
    ],
    advanced: [
      { label: "Bias correction", value: "enabled", note: "Review scanner/site assumptions." },
      { label: "Tissue priors", value: "template defaults", note: "Environment-dependent." },
    ],
  },
  {
    id: "normalization",
    nameKey: "preprocessing.stageNormalization",
    descriptionKey: "preprocessing.stageNormalizationDescription",
    basic: [
      { label: "Template", value: "MNI", note: "Standard space target." },
      { label: "Voxel size", value: "3 x 3 x 3", unit: "mm", note: "Typical rs-fMRI output." },
    ],
    advanced: [
      { label: "Warp regularization", value: "default", note: "SPM deformation setting." },
      { label: "Bounding box", value: "template", note: "Review before execution." },
    ],
  },
  {
    id: "smoothing",
    nameKey: "preprocessing.stageSmoothing",
    descriptionKey: "preprocessing.stageSmoothingDescription",
    basic: [
      { label: "FWHM", value: "6", unit: "mm", range: "4-8", note: "Common rs-fMRI default." },
      { label: "Apply to", value: "normalized BOLD", note: "After spatial normalization." },
    ],
    advanced: [
      { label: "Kernel shape", value: "Gaussian", note: "SPM-compatible." },
      { label: "Mask policy", value: "preserve brain mask", note: "Avoid silent extrapolation." },
    ],
  },
  {
    id: "nuisance-regression",
    nameKey: "preprocessing.stageNuisance",
    descriptionKey: "preprocessing.stageNuisanceDescription",
    basic: [
      { label: "Motion model", value: "6 motion parameters", note: "Basic confound model." },
      { label: "Physiology", value: "WM + CSF", note: "Requires masks." },
    ],
    advanced: [
      { label: "Scrubbing", value: "FD-based", note: "Threshold reviewed in QC." },
      { label: "Polynomial terms", value: "linear", note: "Avoid overfitting by default." },
    ],
  },
  {
    id: "temporal-filtering",
    nameKey: "preprocessing.stageFiltering",
    descriptionKey: "preprocessing.stageFilteringDescription",
    basic: [
      { label: "Band", value: "0.01-0.08", unit: "Hz", note: "Canonical rs-fMRI band." },
      { label: "Detrend", value: "linear", note: "Review with nuisance model." },
    ],
    advanced: [
      { label: "Filter edge", value: "pad and trim", note: "Backend-defined behavior." },
      { label: "Order", value: "automatic", note: "Document final backend choice." },
    ],
  },
  {
    id: "derived-measures",
    nameKey: "preprocessing.stageMeasures",
    descriptionKey: "preprocessing.stageMeasuresDescription",
    basic: [
      { label: "Metrics", value: "ALFF / ReHo / FC", note: "Computed in validated kernels only." },
      { label: "Capability", value: "review required", note: "Do not mark validated by UI alone." },
    ],
    advanced: [
      { label: "Atlas", value: "not selected", note: "Required for atlas FC." },
      { label: "Precision", value: "backend default", note: "Record in provenance." },
    ],
  },
];

const parameterLabelKeys: Record<string, MessageKey> = {
  "Input dataset": "preprocessing.param.inputDataset",
  "Subject scope": "preprocessing.param.subjectScope",
  "BIDS filter": "preprocessing.param.bidsFilter",
  "Derivative root": "preprocessing.param.derivativeRoot",
  TR: "preprocessing.param.tr",
  "Reference slice": "preprocessing.param.referenceSlice",
  "Slice order": "preprocessing.param.sliceOrder",
  "Acquisition timing": "preprocessing.param.acquisitionTiming",
  Realign: "preprocessing.param.realign",
  "FD threshold": "preprocessing.param.fdThreshold",
  Interpolation: "preprocessing.param.interpolation",
  Quality: "preprocessing.param.quality",
  Alignment: "preprocessing.param.alignment",
  Preview: "preprocessing.param.preview",
  "Cost function": "preprocessing.param.costFunction",
  Sampling: "preprocessing.param.sampling",
  "Tissue classes": "preprocessing.param.tissueClasses",
  "T1w source": "preprocessing.param.t1wSource",
  "Bias correction": "preprocessing.param.biasCorrection",
  "Tissue priors": "preprocessing.param.tissuePriors",
  Template: "preprocessing.param.template",
  "Voxel size": "preprocessing.param.voxelSize",
  "Warp regularization": "preprocessing.param.warpRegularization",
  "Bounding box": "preprocessing.param.boundingBox",
  FWHM: "preprocessing.param.fwhm",
  "Apply to": "preprocessing.param.applyTo",
  "Kernel shape": "preprocessing.param.kernelShape",
  "Mask policy": "preprocessing.param.maskPolicy",
  "Motion model": "preprocessing.param.motionModel",
  Physiology: "preprocessing.param.physiology",
  Scrubbing: "preprocessing.param.scrubbing",
  "Polynomial terms": "preprocessing.param.polynomialTerms",
  Band: "preprocessing.param.band",
  Detrend: "preprocessing.param.detrend",
  "Filter edge": "preprocessing.param.filterEdge",
  Order: "preprocessing.param.order",
  Metrics: "preprocessing.param.metrics",
  Capability: "preprocessing.param.capability",
  Atlas: "preprocessing.param.atlas",
  Precision: "preprocessing.param.precision",
};

const parameterValueKeys: Record<string, MessageKey> = {
  "Registered BIDS/NIfTI": "preprocessing.value.registeredBids",
  "All registered subjects": "preprocessing.value.allSubjects",
  "project derivatives": "preprocessing.value.projectDerivatives",
  "from sidecar": "preprocessing.value.fromSidecar",
  middle: "preprocessing.value.middle",
  "sidecar timing": "preprocessing.value.sidecarTiming",
  enabled: "preprocessing.value.enabled",
  "QC overlay": "preprocessing.value.qcOverlay",
  "registered anatomical": "preprocessing.value.registeredAnatomical",
  "template defaults": "preprocessing.value.templateDefaults",
  default: "preprocessing.value.default",
  "normalized BOLD": "preprocessing.value.normalizedBold",
  "preserve brain mask": "preprocessing.value.preserveMask",
  "6 motion parameters": "preprocessing.value.motionParameters",
  "FD-based": "preprocessing.value.fdBased",
  linear: "preprocessing.value.linear",
  "pad and trim": "preprocessing.value.padTrim",
  automatic: "preprocessing.value.automatic",
  "review required": "preprocessing.value.reviewRequired",
  "not selected": "preprocessing.value.notSelected",
  "backend default": "preprocessing.value.backendDefault",
};

const parameterNoteKeys: Record<string, MessageKey> = {
  "Required before planning.": "preprocessing.note.requiredPlanning",
  "Review exclusions later.": "preprocessing.note.reviewExclusions",
  "Default functional input pattern.": "preprocessing.note.defaultFunctional",
  "Must stay under project.": "preprocessing.note.projectOnly",
  "Loaded from BIDS JSON when present.": "preprocessing.note.loadedSidecar",
  "Common default for review.": "preprocessing.note.commonReference",
  "Fallback requires manual review.": "preprocessing.note.manualFallback",
  "No inference in UI.": "preprocessing.note.noInference",
  "Dry-run before execution.": "preprocessing.note.dryRunFirst",
  "QC flag only.": "preprocessing.note.qcFlag",
  "SPM-style parameter.": "preprocessing.note.spmParameter",
  "Registration quality setting.": "preprocessing.note.registrationQuality",
  "Requires anatomical input.": "preprocessing.note.requiresAnatomical",
  "Review before downstream use.": "preprocessing.note.reviewDownstream",
  "Normalized mutual information.": "preprocessing.note.normalizedMutual",
  "SPM-style separation.": "preprocessing.note.spmSeparation",
  "Used by nuisance model.": "preprocessing.note.nuisanceModel",
  "Required for segmentation.": "preprocessing.note.requiredSegmentation",
  "Review scanner/site assumptions.": "preprocessing.note.reviewScanner",
  "Environment-dependent.": "preprocessing.note.environmentDependent",
  "Standard space target.": "preprocessing.note.standardTarget",
  "Typical rs-fMRI output.": "preprocessing.note.typicalOutput",
  "SPM deformation setting.": "preprocessing.note.spmDeformation",
  "Review before execution.": "preprocessing.note.reviewExecution",
  "Common rs-fMRI default.": "preprocessing.note.commonRsfmri",
  "After spatial normalization.": "preprocessing.note.afterNormalization",
  "SPM-compatible.": "preprocessing.note.spmCompatible",
  "Avoid silent extrapolation.": "preprocessing.note.avoidExtrapolation",
  "Basic confound model.": "preprocessing.note.basicConfound",
  "Requires masks.": "preprocessing.note.requiresMasks",
  "Threshold reviewed in QC.": "preprocessing.note.thresholdQc",
  "Avoid overfitting by default.": "preprocessing.note.avoidOverfitting",
  "Canonical rs-fMRI band.": "preprocessing.note.canonicalBand",
  "Review with nuisance model.": "preprocessing.note.reviewNuisance",
  "Backend-defined behavior.": "preprocessing.note.backendBehavior",
  "Document final backend choice.": "preprocessing.note.documentBackend",
  "Computed in validated kernels only.": "preprocessing.note.validatedKernels",
  "Do not mark validated by UI alone.": "preprocessing.note.uiNotValidation",
  "Required for atlas FC.": "preprocessing.note.requiredAtlasFc",
  "Record in provenance.": "preprocessing.note.recordProvenance",
};

function emptyProjectInventory(
  dataState: ProjectDataState | undefined,
  t: I18nContextValue["t"],
): ProjectInventory {
  const resolvedState = dataState ?? "unknown";
  return {
    projectName: t("preprocessing.noInventory"),
    modality: "rs-fMRI",
    dataState: resolvedState,
    dataStateLabel:
      resolvedState === "raw_dicom"
        ? t("preprocessing.rawDicom")
        : resolvedState === "mixed"
          ? t("preprocessing.mixed")
          : resolvedState === "converted_bids"
            ? t("preprocessing.converted")
            : t("preprocessing.emptyProject"),
    stateSentence: t("preprocessing.inventoryNotLoaded"),
    rawDicomCandidates: 0,
    dicomSeriesCount: 0,
    dicomFileCount: 0,
    convertedSubjects: 0,
    niftiFileCount: 0,
    hasRawDicom: resolvedState === "raw_dicom",
    hasConvertedData: false,
    metadataOnlyNiftiInventory: false,
  };
}

function InputReadinessCard({ inventory }: { inventory: ProjectInventory }) {
  const { t } = useI18n();
  return (
    <Card className={styles.readinessCard}>
      <div className={styles.sectionHeader}>
        <div>
          <h3>{t("preprocessing.inputReadiness")}</h3>
          <p>{t("preprocessing.inventoryDerived")}</p>
        </div>
      </div>
      <div className={styles.readinessMetrics} aria-label={t("preprocessing.inputReadiness")}>
        <div>
          <span>{t("preprocessing.dataState")}</span>
          <strong>{inventory.dataStateLabel}</strong>
        </div>
        <div>
          <span>{t("preprocessing.subjects")}</span>
          <strong>{inventory.convertedSubjects}</strong>
        </div>
        <div>
          <span>{t("preprocessing.niftiFiles")}</span>
          <strong>{inventory.niftiFileCount.toLocaleString()}</strong>
        </div>
      </div>
    </Card>
  );
}

function PreprocessingStageOverview({
  configMode,
  hasPreprocessingRun,
  inventory,
  isMissingRegistration,
  onConfigModeChange,
  onSelectStage,
  selectedStageName,
}: {
  configMode: ConfigMode;
  hasPreprocessingRun: boolean;
  inventory: ProjectInventory;
  isMissingRegistration: boolean;
  onConfigModeChange: (mode: ConfigMode) => void;
  onSelectStage: (stageName: string) => void;
  selectedStageName: string;
}) {
  const { t } = useI18n();
  const selectedStage =
    preprocessingStages.find((stage) => stage.id === selectedStageName) ?? preprocessingStages[0];
  const activeParams = configMode === "basic" ? selectedStage.basic : selectedStage.advanced;

  return (
    <section className={styles.overviewGrid} aria-labelledby="preprocessing-stage-title">
      <Card className={styles.flowCard} tone="muted">
        <div className={styles.sectionHeader}>
          <div>
            <h3 id="preprocessing-stage-title">{t("preprocessing.stages")}</h3>
            <p>{t("preprocessing.stagesDescription")}</p>
          </div>
          <Badge
            tone={isMissingRegistration ? "warning" : hasPreprocessingRun ? "info" : "success"}
          >
            {isMissingRegistration
              ? t("preprocessing.inputRequired")
              : hasPreprocessingRun
                ? t("preprocessing.review")
                : t("preprocessing.ready")}
          </Badge>
        </div>
        <ol className={styles.stageList} aria-label={t("preprocessing.stages")}>
          {preprocessingStages.map((stage, index) => {
            const status = stageStatus(index, isMissingRegistration, hasPreprocessingRun);
            return (
              <li
                className={styles.stageItem}
                data-selected={stage.id === selectedStage.id ? "true" : "false"}
                key={stage.id}
              >
                <button
                  type="button"
                  className={styles.stageSelectButton}
                  onClick={() => onSelectStage(stage.id)}
                  aria-label={t("preprocessing.inspectStage", { name: t(stage.nameKey) })}
                >
                  <span className={styles.stageIndex}>{index + 1}</span>
                </button>
                <div className={styles.stageBody}>
                  <div className={styles.stageTitleRow}>
                    <strong>{t(stage.nameKey)}</strong>
                    <Badge tone={stageStatusTone(status)} size="sm">
                      {stageStatusLabel(status, t)}
                    </Badge>
                  </div>
                  <p>{t(stage.descriptionKey)}</p>
                  <dl className={styles.stageConfig}>
                    <div>
                      <dt>{t("preprocessing.basic")}</dt>
                      <dd>{summarizeParameters(stage.basic, t)}</dd>
                    </div>
                    <div>
                      <dt>{t("preprocessing.advanced")}</dt>
                      <dd>{summarizeParameters(stage.advanced, t)}</dd>
                    </div>
                  </dl>
                </div>
              </li>
            );
          })}
        </ol>
      </Card>

      <div className={styles.supportStack}>
        <Card className={styles.configCard} aria-label={t("preprocessing.selectedConfiguration")}>
          <div className={styles.sectionHeader}>
            <div>
              <h3>{t(selectedStage.nameKey)}</h3>
              <p>{t(selectedStage.descriptionKey)}</p>
            </div>
          </div>
          <div
            className={styles.configModeSwitch}
            aria-label={t("preprocessing.configurationMode")}
          >
            <button
              type="button"
              aria-pressed={configMode === "basic"}
              onClick={() => onConfigModeChange("basic")}
            >
              {t("preprocessing.basic")}
            </button>
            <button
              type="button"
              aria-pressed={configMode === "advanced"}
              onClick={() => onConfigModeChange("advanced")}
            >
              {t("preprocessing.advanced")}
            </button>
          </div>
          <dl className={styles.paramList}>
            {activeParams.map((param) => (
              <div key={`${configMode}-${param.label}`}>
                <dt>
                  <span>{localizeParameterText(param.label, parameterLabelKeys, t)}</span>
                  {param.range ? <small>{param.range}</small> : null}
                </dt>
                <dd>
                  <strong>{localizeParameterText(param.value, parameterValueKeys, t)}</strong>
                  {param.unit ? <span>{param.unit}</span> : null}
                  <p>{localizeParameterText(param.note, parameterNoteKeys, t)}</p>
                </dd>
              </div>
            ))}
          </dl>
        </Card>

        <InputReadinessCard inventory={inventory} />

        <Card className={styles.progressiveCard}>
          <div className={styles.sectionHeader}>
            <div>
              <h3>{t("preprocessing.progressive")}</h3>
              <p>{t("preprocessing.progressiveDescription")}</p>
            </div>
          </div>
          <div className={styles.modeList} aria-label={t("preprocessing.configurationModes")}>
            <div>
              <Badge tone="info" size="sm">
                {t("preprocessing.basic")}
              </Badge>
              <p>{t("preprocessing.basicDescription")}</p>
            </div>
            <div>
              <Badge tone="neutral" size="sm">
                {t("preprocessing.advanced")}
              </Badge>
              <p>{t("preprocessing.advancedDescription")}</p>
            </div>
            <div>
              <Badge tone="warning" size="sm">
                {t("preprocessing.safety")}
              </Badge>
              <p>{t("preprocessing.safetyDescription")}</p>
            </div>
          </div>
        </Card>
      </div>
    </section>
  );
}

function summarizeParameters(parameters: ConfigParameter[], t: I18nContextValue["t"]): string {
  return parameters
    .map((parameter) => localizeParameterText(parameter.label, parameterLabelKeys, t))
    .join(", ");
}

function localizeParameterText(
  value: string,
  keys: Record<string, MessageKey>,
  t: I18nContextValue["t"],
): string {
  const key = keys[value];
  return key ? t(key) : value;
}

function stageStatus(
  index: number,
  isMissingRegistration: boolean,
  hasPreprocessingRun: boolean,
): StageStatus {
  if (isMissingRegistration) {
    return index === 0 ? "configure" : "locked";
  }
  if (hasPreprocessingRun) {
    return index === 0 ? "registered" : "review";
  }
  return index === 0 ? "registered" : "configure";
}

function stageStatusLabel(status: StageStatus, t: I18nContextValue["t"]): string {
  const labels: Record<StageStatus, string> = {
    registered: t("preprocessing.statusRegistered"),
    configure: t("preprocessing.statusConfigure"),
    waiting: t("preprocessing.statusWaiting"),
    review: t("preprocessing.statusReview"),
    locked: t("preprocessing.locked"),
  };
  return labels[status];
}

function stageStatusTone(status: StageStatus): "neutral" | "info" | "success" | "warning" {
  if (status === "registered") return "success";
  if (status === "configure") return "info";
  if (status === "locked") return "warning";
  return "neutral";
}
import { useState } from "react";
