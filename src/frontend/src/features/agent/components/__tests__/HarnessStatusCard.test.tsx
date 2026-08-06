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
          }}
        />
      </I18nProvider>,
    );

    expect(screen.getByRole("heading", { name: "规划追踪" })).toBeInTheDocument();
    expect(screen.getByText("等待你处理")).toBeInTheDocument();
    expect(screen.getByText("模型调用：1/6；动作提议：1/8。")).toBeInTheDocument();
    expect(screen.getByText("需要确认图谱。")).toBeInTheDocument();
  });
});
