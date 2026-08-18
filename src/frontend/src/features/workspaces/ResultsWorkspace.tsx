import type { ReactNode } from "react";

import { ArtifactBrowser } from "../../components/ArtifactBrowser";
import { Card, EmptyState } from "../../components/ui";
import type { ArtifactSelection } from "../../lib/workspaceSelection";
import { WorkspaceHeader } from "../dashboard/DashboardChrome";
import styles from "./ResultsWorkspace.module.css";
import layoutStyles from "./WorkspaceLayout.module.css";
import { useI18n } from "../../i18n/useI18n";

export interface ResultsWorkspaceProps {
  baseUrl: string;
  projectId: string | null;
  onSelectedArtifactChange?: (artifact: ArtifactSelection | null) => void;
  viewer?: ReactNode;
}

export function ResultsWorkspace({
  baseUrl,
  projectId,
  onSelectedArtifactChange,
  viewer,
}: ResultsWorkspaceProps) {
  const { t } = useI18n();
  const hasProject = Boolean(projectId);

  return (
    <div className={layoutStyles.stack}>
      <WorkspaceHeader
        title={t("results.title")}
        subtitle={t("results.subtitle")}
        status={hasProject ? t("results.artifacts") : t("results.selectProject")}
      />

      {!hasProject ? (
        <EmptyState title={t("results.selectTitle")} description={t("results.selectDescription")} />
      ) : (
        <section className={styles.artifactViewerGrid} aria-label={t("results.browserViewer")}>
          <Card className={styles.artifactListCard}>
            <ArtifactBrowser
              baseUrl={baseUrl}
              projectId={projectId}
              onSelectedArtifactChange={onSelectedArtifactChange}
            />
          </Card>
          <Card className={styles.viewerCard}>
            {viewer ?? (
              <EmptyState
                title={t("results.noPreview")}
                description={t("results.noPreviewDescription")}
              />
            )}
          </Card>
        </section>
      )}
    </div>
  );
}
