import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { I18nProvider } from "../../../../i18n/I18nProvider";
import { HarnessStatusCard } from "../HarnessStatusCard";

describe("HarnessStatusCard", () => {
  it("renders only the backend-projected status, budget, and redacted trace summary", () => {
    render(
      <I18nProvider locale="zh-CN">
        <HarnessStatusCard
          summary={{
            status: "WAITING_FOR_USER",
            model_calls_used: 1,
            model_calls_limit: 6,
            tool_proposals_used: 1,
            tool_proposals_limit: 8,
            next_step: null,
            terminal_reason: null,
            latest_step_id: "step-1",
            latest_step_summary: "需要确认图谱。",
            last_wake_reason: "create",
            yield_count: 0,
            fallback_from: null,
            fallback_to: null,
            fallback_reason: null,
          }}
        />
      </I18nProvider>,
    );

    expect(screen.getByRole("heading", { name: "规划追踪" })).toBeInTheDocument();
    expect(screen.getByText("等待你处理")).toBeInTheDocument();
    expect(screen.getByText("模型调用：1/6；动作提议：1/8。")).toBeInTheDocument();
    expect(screen.getByText("需要确认图谱。")).toBeInTheDocument();
  });

  it("renders the next step and maps a structured stop code through i18n", () => {
    render(
      <I18nProvider locale="en">
        <HarnessStatusCard
          summary={{
            status: "STOPPED",
            model_calls_used: 1,
            model_calls_limit: 6,
            tool_proposals_used: 0,
            tool_proposals_limit: 8,
            next_step: "step 2",
            terminal_reason: "AGENT_HARNESS_PROVIDER_UNAVAILABLE",
            latest_step_id: "step-1",
            latest_step_summary: "The provider request stopped safely.",
            last_wake_reason: "create",
            yield_count: 1,
            fallback_from: "openai_compatible",
            fallback_to: "deterministic_goal_planner",
            fallback_reason: "AGENT_HARNESS_PROVIDER_UNAVAILABLE",
          }}
        />
      </I18nProvider>,
    );

    expect(screen.getByText("Next step: step 2")).toBeInTheDocument();
    expect(
      screen.getByText("Stopped safely: the configured provider is unavailable"),
    ).toBeInTheDocument();
    expect(screen.queryByText("AGENT_HARNESS_PROVIDER_UNAVAILABLE")).not.toBeInTheDocument();
    expect(screen.getByText("Fairness yields: 1.")).toBeInTheDocument();
    expect(
      screen.getByText("Planning path: openai_compatible → deterministic_goal_planner."),
    ).toBeInTheDocument();
  });
});
