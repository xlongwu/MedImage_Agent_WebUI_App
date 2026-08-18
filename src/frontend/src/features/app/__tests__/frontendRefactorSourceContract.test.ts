// @ts-expect-error Vitest executes this contract test in Node.
import { readFileSync, readdirSync } from "node:fs";
// @ts-expect-error Vitest executes this contract test in Node.
import { dirname, extname, resolve } from "node:path";
// @ts-expect-error Vitest executes this contract test in Node.
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const contractFile = fileURLToPath(import.meta.url);
const srcDir = resolve(dirname(contractFile), "../../..");

function readSources(directory = srcDir): Array<{ path: string; source: string }> {
  return readdirSync(directory, { withFileTypes: true }).flatMap(
    (entry: { isDirectory(): boolean; name: string }) => {
      const path = resolve(directory, entry.name);
      if (entry.isDirectory()) return readSources(path);
      if (![".ts", ".tsx", ".css"].includes(extname(path))) return [];
      return [{ path, source: readFileSync(path, "utf8") }];
    },
  );
}

describe("frontend refactor source contract", () => {
  it("keeps production source independent from the static design folder and external CDNs", () => {
    const offenders = readSources().filter(
      ({ path, source }) =>
        path !== contractFile && /cdn\.jsdelivr|unpkg|medimage-agent-ui-design/.test(source),
    );
    expect(offenders.map(({ path }) => path)).toEqual([]);
  });

  it("keeps feature workspaces off the legacy API aggregation module", () => {
    const featureDir = resolve(srcDir, "features");
    const offenders = readSources(featureDir).filter(
      ({ path, source }) => path !== contractFile && /lib\/api\/legacy/.test(source),
    );
    expect(offenders.map(({ path }) => path)).toEqual([]);
  });

  it("does not hardcode the old v0.6 product label", () => {
    const offenders = readSources().filter(
      ({ path, source }) => path !== contractFile && /["'`]v0\.6["'`]/.test(source),
    );
    expect(offenders.map(({ path }) => path)).toEqual([]);
  });

  it("keeps ordinary workspaces off legacy direct-execution entry points", () => {
    const ordinarySource = [
      ...readSources(resolve(srcDir, "features", "workspaces")),
      ...readSources(resolve(srcDir, "features", "agent")),
      {
        path: resolve(srcDir, "features", "app", "AppShellView.tsx"),
        source: readFileSync(resolve(srcDir, "features", "app", "AppShellView.tsx"), "utf8"),
      },
    ];
    const forbidden = [
      "window.confirm",
      "approved: true",
      "runConversionDryRun",
      "createPreprocessingRun",
      "runNativeFullPreprocessingDryRun",
      "runRsfmri",
      "runExternalSmoke",
    ];

    const offenders = ordinarySource.filter(({ source }) =>
      forbidden.some((token) => source.includes(token)),
    );

    expect(offenders.map(({ path }) => path)).toEqual([]);
  });
});
