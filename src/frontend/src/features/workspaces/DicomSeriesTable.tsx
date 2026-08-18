import { useEffect, useMemo, useRef, useState } from "react";
import { Badge, Card, Table, TableEmpty } from "../../components/ui";
import type {
  ConversionDryRunResponse,
  ConversionMappingPreview,
  ConversionSourceSummary,
} from "../../types";
import type { ProjectInventory } from "../../lib/projectWorkflow";
import type { DataSeriesSelection } from "../../lib/workspaceSelection";
import { useI18n } from "../../i18n/useI18n";
import type { I18nContextValue } from "../../i18n/context";
import styles from "./DicomSeriesTable.module.css";

type FilterId = "all" | "dicom_series" | "mapped" | "warnings" | "manual";
type DryRunRestoreState = "idle" | "loading" | "restored" | "refresh_required" | "error";

type DicomRow = {
  acquisition: string;
  description: string;
  fileCount: string;
  id: string;
  modality: string;
  sourceKind: "project_summary" | "source_summary" | "mapping_preview";
  statusLabel: string;
  statusTone: "neutral" | "info" | "success" | "warning" | "danger";
  subject: string;
  subjectDetail: string;
  series: string;
  seriesDetail: string;
  warnings: string[];
};

const filters: Array<{ id: FilterId; labelKey: Parameters<I18nContextValue["t"]>[0] }> = [
  { id: "all", labelKey: "data.dicom.all" },
  { id: "dicom_series", labelKey: "data.dicom.seriesFilter" },
  { id: "mapped", labelKey: "data.dicom.mapped" },
  { id: "warnings", labelKey: "data.dicom.warnings" },
  { id: "manual", labelKey: "data.dicom.manualReview" },
];

const VIRTUALIZATION_THRESHOLD = 40;
const VIRTUAL_ROW_HEIGHT = 72;
const VIRTUAL_WINDOW_HEIGHT = 420;
const VIRTUAL_OVERSCAN = 4;

export interface DicomSeriesTableProps {
  dryRun: ConversionDryRunResponse | null;
  error: string;
  inventory: ProjectInventory;
  loading: boolean;
  onReviewSelectionChange?: (selection: DataSeriesSelection | null) => void;
  projectId: string | null;
  restoreMessage?: string;
  restoreState?: DryRunRestoreState;
}

export function DicomSeriesTable({
  dryRun,
  error,
  inventory,
  loading,
  onReviewSelectionChange,
  projectId,
  restoreMessage = "",
  restoreState = "idle",
}: DicomSeriesTableProps) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const [activeFilter, setActiveFilter] = useState<FilterId>("all");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [tableScrollState, setTableScrollState] = useState({ key: "", top: 0 });
  const tableViewportRef = useRef<HTMLDivElement>(null);

  const rows = useMemo(() => buildDicomRows(inventory, dryRun, t), [dryRun, inventory, t]);
  const filteredRows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return rows.filter((row) => {
      const haystack = [
        row.subject,
        row.subjectDetail,
        row.series,
        row.seriesDetail,
        row.modality,
        row.description,
        row.acquisition,
        row.statusLabel,
        row.warnings.join(" "),
      ]
        .join(" ")
        .toLowerCase();
      const matchesQuery = !needle || haystack.includes(needle);
      const matchesFilter =
        activeFilter === "all" ||
        (activeFilter === "dicom_series" &&
          /dicom|series/i.test(
            `${row.modality} ${row.series} ${row.subjectDetail} ${row.sourceKind}`,
          )) ||
        (activeFilter === "mapped" && row.sourceKind === "mapping_preview") ||
        (activeFilter === "warnings" && row.warnings.length > 0) ||
        (activeFilter === "manual" &&
          /manual|low|warning/i.test(`${row.statusLabel} ${row.warnings.join(" ")}`));
      return matchesQuery && matchesFilter;
    });
  }, [activeFilter, query, rows]);

  const activeSelectedIds = useMemo(() => {
    const rowIds = new Set(rows.map((row) => row.id));
    return new Set([...selectedIds].filter((id) => rowIds.has(id)));
  }, [rows, selectedIds]);

  useEffect(() => {
    if (!selectedIds.size) return;
    if (!activeSelectedIds.size) {
      onReviewSelectionChange?.(null);
    }
  }, [activeSelectedIds, onReviewSelectionChange, selectedIds.size]);

  const selectedRows = rows.filter((row) => activeSelectedIds.has(row.id));
  const sourceCount = dryRun?.source_summaries.length ?? (inventory.hasRawDicom ? 1 : 0);
  const mappingCount = dryRun?.mapping_preview.length ?? 0;
  const mappingCountLabel = dryRun
    ? String(mappingCount)
    : restoreState === "loading"
      ? t("data.dicom.loading")
      : t("data.dicom.refreshRequired");
  const manualReviewRows = rows.filter((row) =>
    /manual|required|low/i.test(`${row.statusLabel} ${row.warnings.join(" ")}`),
  );
  const rowWarningMessages = rows.flatMap((row) => row.warnings);
  const warningMessages = [...(dryRun?.warnings ?? []), ...rowWarningMessages];
  const blockingMessages = dryRun?.blocking_issues ?? [];
  const usesVirtualization = filteredRows.length > VIRTUALIZATION_THRESHOLD;
  const tableContentKey = `${activeFilter}\u0000${query}\u0000${dryRun?.checked_at ?? ""}`;
  const tableScrollTop = tableScrollState.key === tableContentKey ? tableScrollState.top : 0;
  const virtualRange = useMemo(() => {
    if (!usesVirtualization) {
      return {
        endIndex: filteredRows.length,
        startIndex: 0,
      };
    }

    const visibleRows = Math.ceil(VIRTUAL_WINDOW_HEIGHT / VIRTUAL_ROW_HEIGHT);
    const maxStartIndex = Math.max(0, filteredRows.length - visibleRows - VIRTUAL_OVERSCAN);
    const startIndex = Math.min(
      Math.max(0, Math.floor(tableScrollTop / VIRTUAL_ROW_HEIGHT) - VIRTUAL_OVERSCAN),
      maxStartIndex,
    );
    const endIndex = Math.min(filteredRows.length, startIndex + visibleRows + VIRTUAL_OVERSCAN * 2);

    return { endIndex, startIndex };
  }, [filteredRows.length, tableScrollTop, usesVirtualization]);
  const renderedRows = usesVirtualization
    ? filteredRows.slice(virtualRange.startIndex, virtualRange.endIndex)
    : filteredRows;
  const topSpacerHeight = usesVirtualization ? virtualRange.startIndex * VIRTUAL_ROW_HEIGHT : 0;
  const bottomSpacerHeight = usesVirtualization
    ? (filteredRows.length - virtualRange.endIndex) * VIRTUAL_ROW_HEIGHT
    : 0;

  useEffect(() => {
    if (tableViewportRef.current) {
      tableViewportRef.current.scrollTop = 0;
    }
  }, [tableContentKey]);

  const toggleRow = (row: DicomRow) => {
    setSelectedIds((current) => {
      const next = new Set([...current].filter((id) => activeSelectedIds.has(id)));
      if (next.has(row.id)) {
        next.delete(row.id);
      } else {
        next.add(row.id);
      }
      return next;
    });

    if (activeSelectedIds.has(row.id)) {
      const fallbackRow = rows.find((item) => item.id !== row.id && activeSelectedIds.has(item.id));
      onReviewSelectionChange?.(fallbackRow ? dicomRowSelection(fallbackRow) : null);
    } else {
      onReviewSelectionChange?.(dicomRowSelection(row));
    }
  };

  return (
    <Card className={styles.panel} tone="muted">
      <div className={styles.header}>
        <div>
          <h3>{t("data.dicom.title")}</h3>
          <p>{t("data.dicom.description")}</p>
        </div>
      </div>

      <div className={styles.summaryStrip} aria-label={t("data.dicom.inventorySummary")}>
        <div className={styles.summaryItem}>
          <span>{t("data.dicom.subjectCandidates")}</span>
          <strong>{inventory.rawDicomCandidates}</strong>
        </div>
        <div className={styles.summaryItem}>
          <span>{t("data.dicom.series")}</span>
          <strong>{inventory.dicomSeriesCount}</strong>
        </div>
        <div className={styles.summaryItem}>
          <span>{t("data.dicom.files")}</span>
          <strong>{inventory.dicomFileCount.toLocaleString()}</strong>
        </div>
        <div className={styles.summaryItem}>
          <span>{t("data.dicom.dryRunMappings")}</span>
          <strong>{mappingCountLabel}</strong>
        </div>
        <div className={styles.summaryItem}>
          <span>{t("data.dicom.manualReview")}</span>
          <strong>{manualReviewRows.length}</strong>
        </div>
      </div>

      {!dryRun ? (
        <div className={styles.statusMessage}>
          {restoreState === "loading"
            ? t("data.dicom.checkingMappings")
            : restoreMessage || t("data.dicom.previewNotLoaded")}
        </div>
      ) : null}
      {error ? (
        <div className={`${styles.statusMessage} ${styles.error}`} role="alert">
          {t("data.dicom.failed", { error })}
        </div>
      ) : null}
      {dryRun ? (
        <ReviewSummary
          blockingMessages={blockingMessages}
          manualReviewCount={manualReviewRows.length}
          status={dryRun.status}
          warningMessages={warningMessages}
        />
      ) : null}

      <div className={styles.toolbar}>
        <div className={styles.search}>
          <label htmlFor="dicom-series-search">{t("data.dicom.search")}</label>
          <input
            id="dicom-series-search"
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("data.dicom.searchPlaceholder")}
            type="search"
            value={query}
          />
        </div>
        <div className={styles.filters} aria-label={t("data.dicom.filters")}>
          {filters.map((filter) => (
            <button
              key={filter.id}
              aria-pressed={activeFilter === filter.id}
              className={styles.filterButton}
              onClick={() => setActiveFilter(filter.id)}
              type="button"
            >
              {t(filter.labelKey)}
            </button>
          ))}
        </div>
      </div>

      <div className={styles.tableWrap}>
        {usesVirtualization ? (
          <div className={styles.virtualizationStatus} role="status">
            {t("data.dicom.renderingRows", {
              start: virtualRange.startIndex + 1,
              end: virtualRange.endIndex,
              total: filteredRows.length,
            })}
          </div>
        ) : null}
        <Table
          aria-rowcount={filteredRows.length}
          caption={t("data.dicom.caption", {
            sources: sourceCount,
            rows: filteredRows.length,
          })}
          viewportClassName={usesVirtualization ? styles.virtualizedViewport : undefined}
          viewportProps={
            usesVirtualization
              ? {
                  "aria-label": t("data.dicom.virtualTable"),
                  onScroll: (event) =>
                    setTableScrollState({
                      key: tableContentKey,
                      top: event.currentTarget.scrollTop,
                    }),
                }
              : undefined
          }
          viewportRef={tableViewportRef}
        >
          <thead>
            <tr>
              <th className={styles.checkboxCell} scope="col">
                {t("data.dicom.select")}
              </th>
              <th scope="col">{t("data.dicom.subject")}</th>
              <th scope="col">{t("data.dicom.seriesSource")}</th>
              <th scope="col">{t("data.dicom.files")}</th>
              <th scope="col">{t("data.dicom.modality")}</th>
              <th scope="col">{t("data.dicom.acquisition")}</th>
              <th scope="col">{t("data.status")}</th>
            </tr>
          </thead>
          <tbody>
            {filteredRows.length === 0 ? (
              <TableEmpty colSpan={7}>
                {emptyFilterMessage(activeFilter, dryRun, restoreState, t)}
              </TableEmpty>
            ) : (
              <>
                {usesVirtualization && topSpacerHeight > 0 ? (
                  <VirtualSpacer height={topSpacerHeight} />
                ) : null}
                {renderedRows.map((row, index) => (
                  <DicomSeriesRow
                    key={row.id}
                    ariaRowIndex={
                      usesVirtualization ? virtualRange.startIndex + index + 2 : undefined
                    }
                    checked={activeSelectedIds.has(row.id)}
                    onToggle={toggleRow}
                    row={row}
                  />
                ))}
                {usesVirtualization && bottomSpacerHeight > 0 ? (
                  <VirtualSpacer height={bottomSpacerHeight} />
                ) : null}
              </>
            )}
          </tbody>
        </Table>
      </div>

      {selectedRows.length > 0 ? (
        <div className={styles.selectionPanel} aria-label={t("data.dicom.selectedSources")}>
          <strong>{t("data.dicom.selectedCount", { count: selectedRows.length })}</strong>
          <p>{t("data.dicom.selectionSafety")}</p>
          <ul className={styles.selectionList}>
            {selectedRows.map((row) => (
              <li key={row.id}>{selectionLabel(row)}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </Card>
  );
}

function emptyFilterMessage(
  activeFilter: FilterId,
  dryRun: ConversionDryRunResponse | null,
  restoreState: DryRunRestoreState,
  t: I18nContextValue["t"],
): string {
  if (!dryRun && (restoreState === "refresh_required" || restoreState === "error")) {
    return t("data.dicom.mappingsMissingFilter");
  }
  if (activeFilter === "dicom_series") {
    return t("data.dicom.noSeriesMatch");
  }
  return t("data.dicom.noSourcesMatch");
}

function selectionLabel(row: DicomRow): string {
  const details = [row.modality, row.series].filter(Boolean).join(" - ");
  return [row.subject, details, row.seriesDetail].filter(Boolean).join(" - ");
}

function ReviewSummary({
  blockingMessages,
  manualReviewCount,
  status,
  warningMessages,
}: {
  blockingMessages: string[];
  manualReviewCount: number;
  status: ConversionDryRunResponse["status"];
  warningMessages: string[];
}) {
  const { t } = useI18n();
  const hasIssues = status !== "ready" || warningMessages.length > 0 || blockingMessages.length > 0;
  const statusTone =
    blockingMessages.length > 0 ? "danger" : status === "warning" ? "warning" : "success";

  return (
    <div className={styles.reviewSummary} aria-label={t("data.dicom.reviewSummary")}>
      <div className={styles.reviewSummaryHeader}>
        <strong>{t("data.dicom.reviewState")}</strong>
        <Badge tone={statusTone} size="sm">
          {status}
        </Badge>
      </div>
      <p>{hasIssues ? t("data.dicom.reviewIssues") : t("data.dicom.reviewReady")}</p>
      <dl className={styles.reviewFacts}>
        <div>
          <dt>{t("data.dicom.warnings")}</dt>
          <dd>{warningMessages.length}</dd>
        </div>
        <div>
          <dt>{t("data.dicom.blockingIssues")}</dt>
          <dd>{blockingMessages.length}</dd>
        </div>
        <div>
          <dt>{t("data.dicom.manualReview")}</dt>
          <dd>{manualReviewCount}</dd>
        </div>
      </dl>
      {blockingMessages.length > 0 || warningMessages.length > 0 ? (
        <ul className={styles.reviewIssueList}>
          {[...blockingMessages, ...warningMessages].slice(0, 4).map((message) => (
            <li key={message}>{message}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function DicomSeriesRow({
  ariaRowIndex,
  checked,
  onToggle,
  row,
}: {
  ariaRowIndex?: number;
  checked: boolean;
  onToggle: (row: DicomRow) => void;
  row: DicomRow;
}) {
  const { t } = useI18n();
  return (
    <tr aria-rowindex={ariaRowIndex}>
      <td className={styles.checkboxCell}>
        <input
          aria-label={t("data.dicom.selectSeries", { series: row.series })}
          checked={checked}
          onChange={() => onToggle(row)}
          type="checkbox"
        />
      </td>
      <td>
        <span className={styles.subjectCell}>
          <strong>{row.subject}</strong>
          <span>{row.subjectDetail}</span>
        </span>
      </td>
      <td>
        <span className={styles.seriesCell}>
          <strong>{row.series}</strong>
          <span>{row.description}</span>
        </span>
      </td>
      <td>{row.fileCount}</td>
      <td>{row.modality}</td>
      <td>
        <span className={styles.muted}>{row.acquisition}</span>
      </td>
      <td>
        <span className={styles.statusCell}>
          <Badge tone={row.statusTone} size="sm">
            {row.statusLabel}
          </Badge>
        </span>
        {row.warnings.length > 0 ? (
          <span className={styles.warningList}>
            {row.warnings.slice(0, 2).map((warning) => (
              <span key={warning}>{warning}</span>
            ))}
          </span>
        ) : null}
      </td>
    </tr>
  );
}

function dicomRowSelection(row: DicomRow): DataSeriesSelection {
  return {
    evidenceLevel: row.sourceKind === "mapping_preview" ? "preview_only" : "metadata_only",
    series: row.series,
    seriesDetail: row.seriesDetail,
    sourceKind: row.sourceKind,
    status: row.statusLabel,
    subject: row.subject,
    subjectDetail: row.subjectDetail,
    warnings: row.warnings,
  };
}

function VirtualSpacer({ height }: { height: number }) {
  return (
    <tr aria-hidden="true" className={styles.virtualSpacer}>
      <td colSpan={7} style={{ height }} />
    </tr>
  );
}

function buildDicomRows(
  inventory: ProjectInventory,
  dryRun: ConversionDryRunResponse | null,
  t: I18nContextValue["t"],
): DicomRow[] {
  if (dryRun?.mapping_preview.length) {
    return dryRun.mapping_preview.map((mapping, index) => mappingToRow(mapping, index, t));
  }

  if (dryRun?.source_summaries.length) {
    return dryRun.source_summaries.map((source) => sourceToRow(source, t));
  }

  if (!inventory.hasRawDicom) {
    return [];
  }

  return [
    {
      acquisition: t("data.dicom.notInspected"),
      description: t("data.dicom.projectSummaryDescription"),
      fileCount: inventory.dicomFileCount.toLocaleString(),
      id: "project-summary",
      modality: inventory.modality,
      sourceKind: "project_summary",
      statusLabel: t("data.dicom.summary"),
      statusTone: inventory.dicomSeriesCount > 0 ? "info" : "warning",
      subject: t("data.dicom.candidates", { count: inventory.rawDicomCandidates }),
      subjectDetail: t("data.dicom.projectDiagnostics"),
      series: t("data.dicom.seriesCount", { count: inventory.dicomSeriesCount }),
      seriesDetail: t("data.dicom.sourceDetectionPending"),
      warnings: inventory.dicomSeriesCount > 0 ? [] : [t("data.dicom.noSeriesMetadata")],
    },
  ];
}

function mappingToRow(
  mapping: ConversionMappingPreview,
  index: number,
  t: I18nContextValue["t"],
): DicomRow {
  const seriesId =
    mapping.source_series_uid ||
    basename(mapping.source_path ?? "") ||
    mapping.suggested_relative_path ||
    `mapping-${index + 1}`;
  const suffix = [mapping.modality, mapping.suffix, mapping.task ? `task-${mapping.task}` : ""]
    .filter(Boolean)
    .join(" / ");
  const needsManual = mapping.confidence === "manual_required" || mapping.confidence === "low";
  const manualWarnings =
    needsManual && mapping.warnings.length === 0
      ? [
          mapping.confidence === "manual_required"
            ? t("data.dicom.manualMapping")
            : t("data.dicom.lowConfidence"),
        ]
      : mapping.warnings;

  return {
    acquisition: mapping.session_id || t("data.dicom.sessionUnassigned"),
    description:
      mapping.suggested_relative_path || mapping.source_path || t("data.dicom.mappingPathPending"),
    fileCount: t("data.dicom.perSeriesPending"),
    id: `mapping-${index}-${seriesId}`,
    modality: suffix || mapping.source_type,
    sourceKind: "mapping_preview",
    statusLabel: mapping.confidence.replace(/_/g, " "),
    statusTone: needsManual ? "warning" : mapping.confidence === "high" ? "success" : "info",
    subject: mapping.subject_id || t("data.dicom.unassigned"),
    subjectDetail: mapping.source_type,
    series: seriesId,
    seriesDetail: mapping.source_series_uid
      ? t("data.dicom.seriesUid")
      : t("data.dicom.sourcePath"),
    warnings: manualWarnings,
  };
}

function sourceToRow(source: ConversionSourceSummary, t: I18nContextValue["t"]): DicomRow {
  const subjectList = source.subject_candidates.slice(0, 3).join(", ");
  return {
    acquisition: t("data.dicom.dryRunSource"),
    description: source.root,
    fileCount: source.file_count.toLocaleString(),
    id: source.source_id,
    modality: source.source_type,
    sourceKind: "source_summary",
    statusLabel: source.exists ? t("data.dicom.detected") : t("data.dicom.missing"),
    statusTone: source.exists ? (source.warnings.length ? "warning" : "success") : "danger",
    subject: subjectList || t("data.dicom.noSubjectCandidates"),
    subjectDetail:
      source.subject_candidates.length > 3
        ? t("data.dicom.moreCandidates", { count: source.subject_candidates.length - 3 })
        : t("data.dicom.candidateCount", { count: source.subject_candidates.length }),
    series: t("data.dicom.seriesCount", { count: source.series_count }),
    seriesDetail: source.source_id,
    warnings: source.warnings,
  };
}

function basename(path: string): string {
  return path.replace(/\\/g, "/").split("/").filter(Boolean).pop() ?? "";
}
