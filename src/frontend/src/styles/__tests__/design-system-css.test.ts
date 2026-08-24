// @ts-expect-error Vitest runs this contract test in Node; the app tsconfig omits Node types.
import { readFileSync, readdirSync } from "node:fs";
// @ts-expect-error Vitest runs this contract test in Node; the app tsconfig omits Node types.
import { dirname, resolve } from "node:path";
// @ts-expect-error Vitest runs this contract test in Node; the app tsconfig omits Node types.
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const stylesDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const srcDir = resolve(stylesDir, "..");

function readStyle(name: string): string {
  return readFileSync(resolve(stylesDir, name), "utf8");
}

function readSource(path: string): string {
  return readFileSync(resolve(srcDir, path), "utf8");
}

type CssDirEntry = {
  isDirectory: () => boolean;
  isFile: () => boolean;
  name: string;
};

function readCssSources(dir = srcDir): Array<{ content: string; path: string }> {
  return (readdirSync(dir, { withFileTypes: true }) as CssDirEntry[]).flatMap((entry) => {
    const entryPath = resolve(dir, entry.name);
    if (entry.isDirectory()) return readCssSources(entryPath);
    if (!entry.isFile() || !entry.name.endsWith(".css")) return [];

    return [
      {
        content: readFileSync(entryPath, "utf8"),
        path: entryPath
          .replace(srcDir, "")
          .replace(/^[\\/]/, "")
          .replace(/\\/g, "/"),
      },
    ];
  });
}

type Rgb = {
  r: number;
  g: number;
  b: number;
};

function readCssBlock(css: string, selector: string): string {
  const escapedSelector = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = css.match(new RegExp(`${escapedSelector}\\s*{(?<body>[\\s\\S]*?)\\n}`));

  return match?.groups?.body ?? "";
}

function readToken(block: string, token: string): string {
  const match = block.match(new RegExp(`--${token}:\\s*([^;]+);`));
  const value = match?.[1]?.trim();

  if (!value) {
    throw new Error(`Missing CSS token --${token}`);
  }

  return value;
}

function parseHexColor(value: string): Rgb {
  const match = value.match(/^#(?<hex>[0-9a-f]{6})$/i);
  const hex = match?.groups?.hex;

  if (!hex) {
    throw new Error(`Expected a 6-digit hex color, received ${value}`);
  }

  const color = Number.parseInt(hex, 16);

  return {
    r: (color >> 16) & 255,
    g: (color >> 8) & 255,
    b: color & 255,
  };
}

function toLinearChannel(channel: number): number {
  const normalized = channel / 255;

  return normalized <= 0.03928 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
}

function relativeLuminance(color: Rgb): number {
  return (
    0.2126 * toLinearChannel(color.r) +
    0.7152 * toLinearChannel(color.g) +
    0.0722 * toLinearChannel(color.b)
  );
}

function contrastRatio(foreground: string, background: string): number {
  const foregroundLuminance = relativeLuminance(parseHexColor(foreground));
  const backgroundLuminance = relativeLuminance(parseHexColor(background));
  const lighter = Math.max(foregroundLuminance, backgroundLuminance);
  const darker = Math.min(foregroundLuminance, backgroundLuminance);

  return (lighter + 0.05) / (darker + 0.05);
}

function expectAaContrast(label: string, foreground: string, background: string): void {
  expect(contrastRatio(foreground, background), label).toBeGreaterThanOrEqual(4.5);
}

describe("design system stylesheets", () => {
  it("defines typography, controls, motion, and dark theme tokens", () => {
    const tokens = readStyle("tokens.css");

    expect(tokens).toContain("--font-family-ui");
    expect(tokens).toContain("--font-size-display");
    expect(tokens).toContain("--font-size-page");
    expect(tokens).toContain("--font-size-section");
    expect(tokens).toContain("--font-size-body");
    expect(tokens).toContain("--font-size-secondary");
    expect(tokens).toContain("--font-size-caption");
    expect(tokens).toContain("--control-height-sm");
    expect(tokens).toContain("--control-height-md");
    expect(tokens).toContain("--control-height-lg");
    expect(tokens).toContain("--motion-duration-fast");
    expect(tokens).toContain("--shell-topbar-height: 52px");
    expect(tokens).toContain("--shell-lifecycle-height: 48px");
    expect(tokens).toContain("--shell-global-rail-width: 56px");
    expect(tokens).toContain("--shell-context-sidebar-width: 280px");
    expect(tokens).toContain("--shell-inspector-width: 336px");
    expect(tokens).toContain("--shell-min-desktop-width: 1024px");
    expect(tokens).toMatch(/\[data-theme="dark"\]\s*{[\s\S]*--color-bg-app/);
  });

  it("keeps the light desktop theme aligned with the approved Apple palette", () => {
    const tokens = readStyle("tokens.css");
    const light = readCssBlock(tokens, ":root");
    const globals = readStyle("globals.css");
    const topBar = readSource("features/dashboard/TopBar.module.css");
    const topBarBlock = readCssBlock(topBar, ".topbar");

    expect(readToken(light, "color-bg-app")).toBe("#f5f5f7");
    expect(readToken(light, "color-bg-sidebar")).toBe("#f7f7f9");
    expect(readToken(light, "color-surface-primary")).toBe("#ffffff");
    expect(readToken(light, "color-text-primary")).toBe("#1d1d1f");
    expect(readToken(light, "color-text-secondary")).toBe("#6e6e73");
    expect(readToken(light, "color-text-tertiary")).toBe("#8e8e93");
    expect(readToken(light, "color-accent")).toBe("#007aff");
    expect(readToken(light, "green")).toBe("#34c759");
    expect(readToken(light, "amber")).toBe("#ff9f0a");
    expect(readToken(light, "red")).toBe("#ff3b30");
    expect(readToken(light, "font-weight-medium")).toBe("500");
    expect(readToken(light, "font-weight-strong")).toBe("600");
    expect(readToken(light, "color-material-toolbar")).toBe("rgba(255, 255, 255, 0.78)");
    expect(globals).toContain("background: var(--color-surface-secondary)");
    expect(topBarBlock).toContain("var(--color-material-toolbar)");
    expect(topBarBlock).toContain("backdrop-filter: saturate(180%) blur(20px)");
    expect(topBarBlock).not.toContain("#1d2026");
  });

  it("keeps generic button styling out of the global compatibility layer", () => {
    const globals = readStyle("globals.css");

    expect(globals).not.toMatch(/button:hover\s*{/);
    expect(globals).not.toContain("translateY(-1px)");
  });

  it("keeps theme text and background tokens above WCAG AA contrast", () => {
    const tokens = readStyle("tokens.css");
    const themes = [
      { name: "light", block: readCssBlock(tokens, ":root") },
      { name: "dark", block: readCssBlock(tokens, '[data-theme="dark"]') },
    ];
    const textTokens = [
      { label: "primary text", token: "color-text-primary" },
      { label: "secondary text", token: "color-text-secondary" },
    ];
    const backgroundTokens = [
      { label: "app background", token: "color-bg-app" },
      { label: "solid surface", token: "surface-solid" },
      { label: "primary surface", token: "color-surface-primary" },
      { label: "secondary surface", token: "color-surface-secondary" },
    ];

    for (const theme of themes) {
      expect(theme.block, `${theme.name} token block`).not.toHaveLength(0);

      for (const text of textTokens) {
        for (const background of backgroundTokens) {
          expectAaContrast(
            `${theme.name} ${text.label} on ${background.label}`,
            readToken(theme.block, text.token),
            readToken(theme.block, background.token),
          );
        }
      }
    }
  });

  it("loads stylesheet layers in token-first order", () => {
    const main = readSource("main.tsx");
    const orderedImports = [
      "./styles/tokens.css",
      "./styles/globals.css",
      "./styles/typography.css",
      "./styles/motion.css",
      "./styles.css",
    ];
    const positions = orderedImports.map((path) => main.indexOf(path));

    expect(positions.every((position) => position >= 0)).toBe(true);
    expect(positions).toEqual([...positions].sort((a, b) => a - b));
  });

  it("documents styles.css as a legacy compatibility layer", () => {
    const legacyStyles = readSource("styles.css");

    expect(legacyStyles).toContain("Legacy compatibility layer");
    expect(legacyStyles).toContain("component CSS modules");
    expect(legacyStyles).toContain("do not add new shell-level selectors");
  });

  it("keeps visible focus outlines available across source stylesheets", () => {
    const globals = readStyle("globals.css");
    const cssSources = readCssSources();
    const outlineRemovalPattern = /outline:\s*(?:none|0)\b/i;
    const offenders = cssSources
      .filter((source) => outlineRemovalPattern.test(source.content))
      .map((source) => source.path);

    expect(globals).toContain("button:focus-visible");
    expect(globals).toContain("outline: 3px solid var(--color-focus-ring)");
    expect(offenders).toEqual([]);
  });

  it("keeps reduced-motion and shell overlap protections explicit", () => {
    const globals = readStyle("globals.css");
    const motion = readStyle("motion.css");
    const legacyStyles = readSource("styles.css");
    const topBarModule = readSource("features/dashboard/TopBar.module.css");
    const appShellModule = readSource("layouts/AppShell/AppShell.module.css");
    const contextInspectorModule = readSource("features/tools/ContextInspector.module.css");
    const topBarBlock = readCssBlock(topBarModule, ".topbar");
    const retiredGlobalSelectors = [
      ".topbar",
      ".side-rail",
      ".workflow-main",
      ".workflow-workspace",
      ".workspace-panel-grid",
      ".workspace-summary-row",
      ".viewer-card",
      ".viewer-dock",
      ".scan-select",
      ".project-switcher",
      ".windows-workstation",
      ".dashboard-frame",
      ".workspace-grid",
      ".workflow-empty-note",
      ".compact-task-list",
      ".compact-retry-button",
      ".activity-details",
      ".activity-summary",
      ".activity-summary-title",
      ".activity-body",
      ".run-activity-shell",
      ".run-activity-bar",
      ".run-activity-status",
      ".run-activity-primary",
      ".run-activity-name",
      ".run-activity-pipeline",
      ".run-activity-progress",
      ".run-activity-count",
      ".run-activity-expand",
      ".run-activity-drawer",
      ".run-activity-drawer-row",
      ".run-activity-row",
      ".run-tone-running",
      ".context-inspector",
      ".context-inspector-header",
      ".context-inspector-title",
      ".context-inspector-close",
      ".context-inspector-body",
    ];

    expect(globals).toContain("font-size: var(--font-size-body)");
    expect(motion).toContain("@media (prefers-reduced-motion: reduce)");
    expect(motion).toContain("--motion-duration-fast: 1ms");
    expect(motion).toContain("animation-delay: 0ms !important");
    expect(motion).toContain("transition-delay: 0ms !important");
    expect(motion).toContain("scroll-behavior: auto");
    expect(motion).toContain(':where(button, [role="button"]):hover');
    expect(topBarBlock).toContain("position: relative");
    expect(topBarBlock).not.toContain("position: sticky");
    expect(appShellModule).toContain(".lifecycleSlot");
    expect(appShellModule).not.toContain("shell-sidebar-width");
    expect(contextInspectorModule).toContain("--shell-topbar-height");
    expect(contextInspectorModule).not.toContain("--topbar-height");
    for (const selector of retiredGlobalSelectors) {
      expect(legacyStyles).not.toContain(selector);
    }
  });

  it("keeps the desktop workspace collapse rules explicit at the supported widths", () => {
    const appShellModule = readSource("layouts/AppShell/AppShell.module.css");

    expect(appShellModule).toContain("grid-template-columns: var(--shell-context-sidebar-width)");
    expect(appShellModule).toContain("@media (max-width: 1439px)");
    expect(appShellModule).toContain("position: fixed");
    expect(appShellModule).toContain("@media (max-width: 1179px)");
    expect(appShellModule).toContain(".contextSidebarSlot {\n    display: none;");
  });
});
