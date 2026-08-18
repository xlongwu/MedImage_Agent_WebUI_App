import { useEffect, useRef, useState } from "react";
import { WorkspaceHeader } from "../dashboard/DashboardChrome";
import BidsValidationPanel from "../../components/BidsValidationPanel";
import type { BidsValidationViewState } from "../../components/BidsValidationPanel";
import DataReadinessPanel from "../../components/DataReadinessPanel";
import type { ProjectInventory } from "../../lib/projectWorkflow";
import type { DataSeriesSelection } from "../../lib/workspaceSelection";
import { getLatestConversionDryRun } from "../../lib/api/dicom";
import type { ConversionDryRunResponse } from "../../types";
import { EvidenceBadge } from "../../components/domain/EvidenceBadge";
import { Badge, Button, Card, EmptyState, Table } from "../../components/ui";
import { TechnicalModuleSection } from "../../components/domain/TechnicalModuleSection";
import { ConversionStepper } from "./ConversionStepper";
import { DicomSeriesTable } from "./DicomSeriesTable";
import styles from "./DataConversionWorkspace.module.css";
import layoutStyles from "./WorkspaceLayout.module.css";
import { useI18n } from "../../i18n/useI18n";

type DryRunRestoreState = "idle" | "loading" | "restored" | "refresh_required" | "error";

export interface DataConversionWorkspaceProps {
  baseUrl: string;
  projectId: string | null;
  inventory: ProjectInventory;
  onSelectedDataSeriesChange?: (selection: DataSeriesSelection | null) => void;
  onOpenAgent?: () => void;
  bidsValidation?: BidsValidationViewState;
}

export function DataConversionWorkspace({
  baseUrl,
  projectId,
  inventory,
  onSelectedDataSeriesChange,
  onOpenAgent,
  bidsValidation,
}: DataConversionWorkspaceProps) {
  const { t } = useI18n();
  const [dryRun, setDryRun] = useState<ConversionDryRunResponse | null>(null);
  const [dryRunError, setDryRunError] = useState("");
  const [dryRunRestoreState, setDryRunRestoreState] = useState<DryRunRestoreState>("idle");
  const [dryRunRestoreMessage, setDryRunRestoreMessage] = useState("");
  const [detailedChecksOpen, setDetailedChecksOpen] = useState(false);
  const dryRunRequestRef = useRef(0);
  const hasRegisteredConvertedInput =
    inventory.hasConvertedData &&
    !inventory.metadataOnlyNiftiInventory &&
    (inventory.convertedSubjects > 0 || inventory.niftiFileCount > 0);
  const isConverted = inventory.dataState === "converted_bids" || hasRegisteredConvertedInput;
  const isRawConversionState = inventory.dataState === "raw_dicom";

  useEffect(() => {
    const requestId = dryRunRequestRef.current + 1;
    dryRunRequestRef.current = requestId;

    if (!projectId || !isRawConversionState) {
      void Promise.resolve().then(() => {
        if (dryRunRequestRef.current !== requestId) return;
        setDryRun(null);
        setDryRunRestoreState("idle");
        setDryRunRestoreMessage("");
      });
      return;
    }

    void Promise.resolve().then(async () => {
      if (dryRunRequestRef.current !== requestId) return;
      setDryRun(null);
      setDryRunError("");
      setDryRunRestoreState("loading");
      setDryRunRestoreMessage(t("data.checkingDryRun"));

      try {
        const response = await getLatestConversionDryRun(baseUrl, projectId);
        if (dryRunRequestRef.current !== requestId) return;
        if (response.ok && response.mapping_preview.length > 0) {
          setDryRun(response);
          setDryRunRestoreState("restored");
          setDryRunRestoreMessage(t("data.restoredDryRun"));
          return;
        }
        setDryRun(null);
        setDryRunRestoreState("refresh_required");
        setDryRunRestoreMessage(
          response.blocking_issues[0]
            ? `Dry-run preview not loaded; refresh required. ${response.blocking_issues[0]}`
            : t("data.refreshDryRun"),
        );
      } catch (error) {
        if (dryRunRequestRef.current !== requestId) return;
        setDryRun(null);
        setDryRunRestoreState("error");
        setDryRunRestoreMessage(
          t("data.restoreDryRunFailed", {
            error: error instanceof Error ? error.message : String(error),
          }),
        );
      }
    });
  }, [baseUrl, isRawConversionState, projectId, t]);

  if (isConverted) {
    return (
      <div className={layoutStyles.stack}>
        <WorkspaceHeader
          title={t("data.title")}
          subtitle={
            inventory.dataState === "mixed"
              ? t("data.convertedMixedSubtitle")
              : t("data.convertedSubtitle")
          }
          status={t("data.ready")}
        />
        <div className={layoutStyles.modeNote}>
          {inventory.dataState === "mixed" ? t("data.mixedModeNote") : t("data.convertedModeNote")}
        </div>
        <ConvertedInventorySummary inventory={inventory} />
        <div className={layoutStyles.summaryRow}>
          <div>
            <span>{t("data.primaryAction")}</span>
            <strong>{t("data.validateInventory")}</strong>
          </div>
          <div>
            <span>{t("data.nextWorkspace")}</span>
            <strong>{t("data.preprocessingOrQc")}</strong>
          </div>
        </div>
        <div className={layoutStyles.panelGrid}>
          <div id="bids-validation-panel">
            <BidsValidationPanel
              baseUrl={baseUrl}
              projectId={projectId}
              projectState={inventory.dataState}
              validation={bidsValidation}
            />
          </div>
        </div>
        <DetailedDataChecks
          baseUrl={baseUrl}
          includeBidsValidation={false}
          includeConversionReview={inventory.dataState === "mixed"}
          inventory={inventory}
          isOpen={detailedChecksOpen}
          onToggle={() => setDetailedChecksOpen((open) => !open)}
          projectId={projectId}
          bidsValidation={bidsValidation}
        />
      </div>
    );
  }

  return (
    <div className={layoutStyles.stack}>
      <WorkspaceHeader
        title={t("data.title")}
        subtitle={t("data.subtitle")}
        status={
          inventory.hasRawDicom
            ? t("data.expectedBeforeConversion")
            : inventory.hasConvertedData
              ? t("data.ready")
              : t("data.notStarted")
        }
      />
      {inventory.dataState === "mixed" && (
        <div className={`${layoutStyles.modeNote} ${layoutStyles.modeNoteSpaced}`}>
          <strong>{t("data.mixedNoticeLabel")}</strong> {t("data.mixedNotice")}
        </div>
      )}
      {isRawConversionState ? (
        <div className={styles.rawWorkspace}>
          <div className={styles.rawMain}>
            {onOpenAgent ? (
              <Button onClick={onOpenAgent} variant="primary">
                {t("agent.title")}
              </Button>
            ) : null}
            <DicomSeriesTable
              dryRun={dryRun}
              error={dryRunError}
              inventory={inventory}
              loading={dryRunRestoreState === "loading"}
              onReviewSelectionChange={onSelectedDataSeriesChange}
              projectId={projectId}
              restoreMessage={dryRunRestoreMessage}
              restoreState={dryRunRestoreState}
            />
          </div>
          <aside className={styles.rawAside} aria-label={t("data.conversionReadiness")}>
            <ConversionStepper dryRun={dryRun} error={dryRunError} inventory={inventory} />
          </aside>
        </div>
      ) : (
        <EmptyDataState />
      )}
      <DetailedDataChecks
        baseUrl={baseUrl}
        includeBidsValidation={true}
        includeConversionReview={isRawConversionState}
        inventory={inventory}
        isOpen={detailedChecksOpen}
        onToggle={() => setDetailedChecksOpen((open) => !open)}
        projectId={projectId}
        bidsValidation={bidsValidation}
      />
    </div>
  );
}

function DetailedDataChecks({
  baseUrl,
  includeBidsValidation,
  includeConversionReview,
  inventory,
  isOpen,
  onToggle,
  projectId,
  bidsValidation,
}: {
  baseUrl: string;
  includeBidsValidation: boolean;
  includeConversionReview: boolean;
  inventory: ProjectInventory;
  isOpen: boolean;
  onToggle: () => void;
  projectId: string | null;
  bidsValidation?: BidsValidationViewState;
}) {
  const { t } = useI18n();
  const isEmpty = inventory.dataState === "empty" || inventory.dataState === "unknown";
  const status = isOpen ? t("data.openForReview") : t("data.collapsed");
  const helperText = isEmpty
    ? t("data.detailedChecksEmptyHelp")
    : includeConversionReview
      ? t("data.detailedChecksReviewHelp")
      : t("data.detailedChecksConvertedHelp");

  return (
    <TechnicalModuleSection
      ariaLabel={t("data.detailedChecks")}
      bodyClassName={layoutStyles.panelGrid}
      description={t("data.detailedChecksDescription")}
      evidenceLevel={isEmpty ? "backend_required" : "metadata_only"}
      helperText={helperText}
      hideActionLabel={t("data.hideDetailedChecks")}
      isOpen={isOpen}
      onToggle={onToggle}
      openLabel={t("data.openDetailedChecks")}
      safetyNote={t("data.detailedChecksSafety")}
      status={status}
      statusTone={isOpen ? "info" : "neutral"}
      title={t("data.detailedChecks")}
    >
      <div id="data-readiness-panel">
        <DataReadinessPanel
          baseUrl={baseUrl}
          projectId={projectId}
          projectState={inventory.dataState}
        />
      </div>
      {includeBidsValidation ? (
        <div id="bids-validation-panel">
          <BidsValidationPanel
            baseUrl={baseUrl}
            projectId={projectId}
            projectState={inventory.dataState}
            validation={bidsValidation}
          />
        </div>
      ) : null}
    </TechnicalModuleSection>
  );
}

function ConvertedInventorySummary({ inventory }: { inventory: ProjectInventory }) {
  const { t } = useI18n();
  return (
    <Card className={styles.summaryCard} tone="muted">
      <div className={styles.cardHeader}>
        <div>
          <h3>{t("data.convertedInventory")}</h3>
          <p>{t("data.convertedInventoryDescription")}</p>
        </div>
        <Badge tone="success">{inventory.dataStateLabel}</Badge>
      </div>
      <Table caption={t("data.convertedReadinessCaption")}>
        <thead>
          <tr>
            <th>{t("data.scope")}</th>
            <th>{t("data.evidence")}</th>
            <th>{t("data.status")}</th>
            <th>{t("data.nextAction")}</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>{t("data.convertedSubjects")}</td>
            <td>{inventory.convertedSubjects}</td>
            <td>
              <Badge tone="success" size="sm">
                {t("data.registered")}
              </Badge>
            </td>
            <td>{t("data.checkPreprocessing")}</td>
          </tr>
          <tr>
            <td>{t("data.niftiFiles")}</td>
            <td>{inventory.niftiFileCount.toLocaleString()}</td>
            <td>
              <EvidenceBadge
                level={inventory.metadataOnlyNiftiInventory ? "metadata_only" : "created"}
                size="sm"
              />
            </td>
            <td>{t("data.reviewValidationQc")}</td>
          </tr>
        </tbody>
      </Table>
    </Card>
  );
}

function EmptyDataState() {
  const { t } = useI18n();
  return <EmptyState title={t("data.emptyTitle")} description={t("data.emptyDescription")} />;
}
