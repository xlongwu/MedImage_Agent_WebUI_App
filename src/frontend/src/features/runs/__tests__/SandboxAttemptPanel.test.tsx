import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { I18nProvider } from "../../../i18n/I18nProvider";
import type { SandboxAttempt } from "../../../lib/types/sandbox";
import { SandboxAttemptPanel } from "../components/SandboxAttemptPanel";

function attempt(network: SandboxAttempt["network_isolation"]): SandboxAttempt {
  return {
    sandbox_id: network,
    run_id: "run",
    node_id: "node",
    subject_id: null,
    status: network === "enforced" ? "SUCCEEDED" : "PREPARED",
    started_at: null,
    ended_at: null,
    result_code: null,
    output_count: 0,
    policy_version: "windows-sandbox-v2",
    network_isolation: network,
  };
}

describe.each(["en", "zh-CN"] as const)("sandbox network truth in %s", (locale) => {
  it("renders measured and unverified attempts independently", () => {
    render(
      <I18nProvider locale={locale}>
        <SandboxAttemptPanel attempts={[attempt("unverified"), attempt("enforced")]} />
      </I18nProvider>,
    );
    expect(screen.getByText(locale === "en" ? /has not been verified/ : /尚未验证/)).toBeVisible();
    expect(
      screen.getByText(locale === "en" ? /Network isolation enforced/ : /已强制实施/),
    ).toBeVisible();
  });

  it("does not infer isolation success from an empty attempt list", () => {
    render(
      <I18nProvider locale={locale}>
        <SandboxAttemptPanel attempts={[]} />
      </I18nProvider>,
    );
    expect(
      screen.queryByText(locale === "en" ? /Network isolation enforced/ : /已强制实施/),
    ).toBeNull();
  });
});
