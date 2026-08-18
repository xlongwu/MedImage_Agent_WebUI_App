import { WorkspaceHeader } from "../dashboard/DashboardChrome";
import EnvironmentHealthPanel from "../../components/EnvironmentHealthPanel";
import { EvidenceBadge } from "../../components/domain/EvidenceBadge";
import { MemorySettingsPanel } from "../memory/MemorySettingsPanel";
import { Badge, Card, SegmentedControl, Table } from "../../components/ui";
import type { LocalePreference, ThemePreference } from "../../hooks/useAppState";
import { useI18n } from "../../i18n/useI18n";
import styles from "./SettingsEnvironmentWorkspace.module.css";
import layoutStyles from "./WorkspaceLayout.module.css";

type Translate = ReturnType<typeof useI18n>["t"];

export interface SettingsEnvironmentWorkspaceProps {
  advancedMode: boolean;
  baseUrl: string;
  onAdvancedModeChange: (enabled: boolean) => void;
  onThemePreferenceChange: (themePreference: ThemePreference) => void;
  projectId: string | null;
  themePreference: ThemePreference;
  localePreference: LocalePreference;
  onLocalePreferenceChange: (localePreference: LocalePreference) => void;
}

export function SettingsEnvironmentWorkspace({
  advancedMode,
  baseUrl,
  onAdvancedModeChange,
  onThemePreferenceChange,
  projectId,
  themePreference,
  localePreference,
  onLocalePreferenceChange,
}: SettingsEnvironmentWorkspaceProps) {
  const { t } = useI18n();
  const settingsDomains = buildSettingsDomains(t);
  const themeOptions = [
    { label: t("settings.themeLight"), value: "light" },
    { label: t("settings.themeDark"), value: "dark" },
  ];
  const generalIntegrationControls = buildGeneralIntegrationControls(t);
  const safetyPolicyRows = buildSafetyPolicyRows(t);

  return (
    <div className={layoutStyles.stack}>
      <WorkspaceHeader
        title={t("settings.title")}
        subtitle={t("settings.subtitle")}
        status={t("settings.planningOnly")}
      />
      <div className="planning-note">{t("settings.planningNote")}</div>

      <MemorySettingsPanel baseUrl={baseUrl} projectId={projectId} />

      <nav className={styles.domainNav} aria-label={t("settings.domains")}>
        {settingsDomains.map((item) => (
          <a href={`#settings-${item.slug}`} key={item.domain}>
            <span>{item.domain}</span>
            <small>{item.navLabel}</small>
          </a>
        ))}
      </nav>

      <section className={styles.settingsGrid} aria-label={t("settings.overview")}>
        <Card className={styles.mapCard} id="settings-general" tone="muted">
          <div className={styles.cardHeader}>
            <div>
              <h3>{t("settings.map")}</h3>
              <p>{t("settings.mapDescription")}</p>
            </div>
            <Badge tone="info">{t("settings.migrated")}</Badge>
          </div>
          <Table caption={t("settings.domains")}>
            <thead>
              <tr>
                <th>{t("settings.domain")}</th>
                <th>{t("settings.scope")}</th>
                <th>{t("settings.executionStance")}</th>
              </tr>
            </thead>
            <tbody>
              {settingsDomains.map((item) => (
                <tr key={item.domain}>
                  <td>{item.domain}</td>
                  <td>{item.scope}</td>
                  <td>
                    <Badge tone={item.tone} size="sm">
                      {item.stance}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>

        <Card className={styles.generalCard} id="settings-integrations">
          <div className={styles.cardHeader}>
            <div>
              <h3>{t("settings.generalIntegrations")}</h3>
              <p>{t("settings.generalDescription")}</p>
            </div>
            <Badge tone="neutral">{t("settings.configSurface")}</Badge>
          </div>
          <div className={styles.preferenceStack} aria-label={t("settings.generalPreferences")}>
            <div className={styles.preferenceRow}>
              <div>
                <span className={styles.preferenceLabel}>{t("settings.language")}</span>
                <p>{t("settings.languageDescription")}</p>
              </div>
              <SegmentedControl
                aria-label={t("settings.language")}
                options={[
                  { label: t("settings.languageEnglish"), value: "en" },
                  { label: t("settings.languageChinese"), value: "zh-CN" },
                ]}
                value={localePreference}
                onChange={(value) => onLocalePreferenceChange(value as LocalePreference)}
              />
            </div>
            <div className={styles.preferenceRow}>
              <div>
                <span className={styles.preferenceLabel}>{t("settings.advancedMode")}</span>
                <p>{t("settings.advancedModeDescription")}</p>
                <p className={styles.preferenceSafety}>{t("settings.advancedModeWarning")}</p>
              </div>
              <SegmentedControl
                aria-label={t("settings.advancedMode")}
                options={[
                  { label: t("common.off"), value: "off" },
                  { label: t("common.on"), value: "on" },
                ]}
                value={advancedMode ? "on" : "off"}
                onChange={(value) => onAdvancedModeChange(value === "on")}
              />
            </div>
            <div className={styles.preferenceRow}>
              <div>
                <span className={styles.preferenceLabel}>{t("settings.theme")}</span>
                <p>{t("settings.themeDescription")}</p>
              </div>
              <SegmentedControl
                aria-label={t("settings.themePreference")}
                options={themeOptions}
                value={themePreference}
                onChange={(value) => onThemePreferenceChange(value as ThemePreference)}
              />
            </div>
          </div>
          <p className={styles.preferenceSafety}>{t("settings.safety")}</p>
          <Table caption={t("settings.generalControls")}>
            <thead>
              <tr>
                <th>{t("settings.setting")}</th>
                <th>{t("settings.currentSurface")}</th>
                <th>{t("settings.authority")}</th>
              </tr>
            </thead>
            <tbody>
              {generalIntegrationControls.map((item) => (
                <tr key={item.setting}>
                  <td>{item.setting}</td>
                  <td>{item.surface}</td>
                  <td>
                    <Badge tone={item.tone} size="sm">
                      {item.authority}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>

        <Card className={styles.safetyCard} id="settings-safety">
          <div className={styles.cardHeader}>
            <div>
              <h3>{t("settings.safetyGates")}</h3>
              <p>{t("settings.safetyDescription")}</p>
            </div>
          </div>
          <dl className={styles.safetyList}>
            <div>
              <dt>{t("settings.externalExecution")}</dt>
              <dd>{t("settings.externalExecutionDescription")}</dd>
            </div>
            <div>
              <dt>{t("settings.rawData")}</dt>
              <dd>{t("settings.rawDataDescription")}</dd>
            </div>
            <div>
              <dt>{t("settings.diagnostics")}</dt>
              <dd>{t("settings.diagnosticsDescription")}</dd>
            </div>
          </dl>
        </Card>

        <Card className={styles.policyCard} id="settings-diagnostics">
          <div className={styles.cardHeader}>
            <div>
              <h3>{t("settings.policyMatrix")}</h3>
              <p>{t("settings.policyDescription")}</p>
            </div>
            <Badge tone="warning">{t("settings.backendOwned")}</Badge>
          </div>
          <Table caption={t("settings.policyMatrix")}>
            <thead>
              <tr>
                <th>{t("settings.policy")}</th>
                <th>{t("settings.uiStance")}</th>
                <th>{t("settings.gate")}</th>
              </tr>
            </thead>
            <tbody>
              {safetyPolicyRows.map((item) => (
                <tr key={item.policy}>
                  <td>{item.policy}</td>
                  <td>{item.stance}</td>
                  <td>
                    <Badge tone={item.tone} size="sm">
                      {item.gate}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      </section>

      {advancedMode ? (
        <section
          className={styles.sectionStack}
          id="settings-environment"
          aria-label={t("settings.environmentModules")}
        >
          <div className={styles.sectionHeader}>
            <div>
              <h3>{t("settings.environmentSetup")}</h3>
              <p>{t("settings.environmentDescription")}</p>
            </div>
            <EvidenceBadge level="metadata_only">{t("settings.readinessOnly")}</EvidenceBadge>
          </div>
          <div className={layoutStyles.panelGrid}>
            <div id="environment-health-panel">
              <EnvironmentHealthPanel baseUrl={baseUrl} />
            </div>
          </div>
        </section>
      ) : null}
    </div>
  );
}

type BadgeTone = "neutral" | "info" | "success" | "warning" | "danger";

function buildSettingsDomains(t: Translate): Array<{
  domain: string;
  navLabel: string;
  slug: string;
  scope: string;
  stance: string;
  tone: BadgeTone;
}> {
  return [
    {
      domain: t("settings.domain.general"),
      navLabel: t("settings.domain.preferences"),
      slug: "general",
      scope: t("settings.domain.generalScope"),
      stance: t("settings.domain.configOnly"),
      tone: "neutral",
    },
    {
      domain: t("settings.domain.environment"),
      navLabel: t("settings.domain.readiness"),
      slug: "environment",
      scope: t("settings.domain.environmentScope"),
      stance: t("settings.domain.readiness"),
      tone: "info",
    },
    {
      domain: t("settings.domain.integrations"),
      navLabel: t("settings.domain.advisory"),
      slug: "integrations",
      scope: t("settings.domain.integrationsScope"),
      stance: t("settings.domain.disabledDefault"),
      tone: "warning",
    },
    {
      domain: t("settings.domain.safety"),
      navLabel: t("settings.domain.backendGates"),
      slug: "safety",
      scope: t("settings.domain.safetyScope"),
      stance: t("settings.domain.backendGated"),
      tone: "warning",
    },
    {
      domain: t("settings.domain.diagnostics"),
      navLabel: t("settings.domain.onDemand"),
      slug: "diagnostics",
      scope: t("settings.domain.diagnosticsScope"),
      stance: t("settings.domain.onDemand"),
      tone: "info",
    },
  ];
}

function buildGeneralIntegrationControls(t: Translate): Array<{
  authority: string;
  setting: string;
  surface: string;
  tone: BadgeTone;
}> {
  return [
    {
      setting: t("settings.control.languageTheme"),
      surface: t("settings.control.desktopModule"),
      authority: t("settings.domain.configOnly"),
      tone: "neutral",
    },
    {
      setting: t("settings.control.startup"),
      surface: t("settings.control.sidecar"),
      authority: t("settings.domain.configOnly"),
      tone: "neutral",
    },
    {
      setting: t("settings.control.llmProvider"),
      surface: t("settings.control.modelPlanner"),
      authority: t("settings.control.advisoryOnly"),
      tone: "info",
    },
    {
      setting: t("settings.control.externalTools"),
      surface: t("settings.control.smokeChecks"),
      authority: t("settings.domain.disabledDefault"),
      tone: "warning",
    },
  ];
}

function buildSafetyPolicyRows(t: Translate): Array<{
  gate: string;
  policy: string;
  stance: string;
  tone: BadgeTone;
}> {
  return [
    {
      policy: t("settings.policy.rawReadOnly"),
      stance: t("settings.policy.rawStance"),
      gate: t("settings.policy.invariant"),
      tone: "danger",
    },
    {
      policy: t("settings.policy.overwrite"),
      stance: t("settings.policy.overwriteStance"),
      gate: t("settings.policy.approval"),
      tone: "warning",
    },
    {
      policy: t("settings.policy.approver"),
      stance: t("settings.policy.approverStance"),
      gate: t("settings.domain.backendGated"),
      tone: "warning",
    },
    {
      policy: t("settings.externalExecution"),
      stance: t("settings.policy.externalStance"),
      gate: t("settings.policy.environmentFlag"),
      tone: "info",
    },
  ];
}
