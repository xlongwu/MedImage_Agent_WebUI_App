import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../../../i18n/I18nProvider";
import { useI18n } from "../../../i18n/useI18n";
import type { SystemMessage } from "../../../lib/types/agentTask";
import { formatSystemMessage } from "../systemMessages";

function Messages({ onAction }: { onAction: () => void }) {
  const { t } = useI18n();
  const messages: SystemMessage[] = [
    { code: "approval.scope.summary", params: { count: 2, subject_ids: ["sub-001", "sub-002"] } },
    { code: "approval.safety.summary", params: {} },
    {
      code: "approval.environment.summary",
      params: { backend_facts: [{ backend_id: "native_python", status: "disabled" }] },
    },
    { code: "decision.template.question", params: {} },
    { code: "resource.name", params: { resource_name: "My registered template" } },
    { code: "science.confirmed", params: { decision_kind: "repetition_time", value: 2 } },
    {
      code: "science.confirmed",
      params: { decision_kind: "global_signal_regression", value: "exclude" },
    },
    { code: "memory.influence", params: { decision_kind: "atlas" } },
    { code: "unknown.future", params: { resource_name: "SECRET_BACKEND_PARAGRAPH" } },
  ];
  return (
    <div data-summary-hash="actual-unchanged-hash">
      {messages.map((item, index) => (
        <p key={`${item.code}:${index}`}>{formatSystemMessage(t, item)}</p>
      ))}
      <button onClick={onAction}>action</button>
    </div>
  );
}

describe("semantic system presentation", () => {
  it("switches locale using the same facts and identity without a command", () => {
    const action = vi.fn();
    const { rerender, container } = render(
      <I18nProvider locale="en">
        <Messages onAction={action} />
      </I18nProvider>,
    );
    expect(screen.getByText(/Approve exactly 2/)).toHaveTextContent("sub-001 · sub-002");
    expect(screen.getByText(/native_python/)).toHaveTextContent("disabled");
    expect(container).not.toHaveTextContent("SECRET_BACKEND_PARAGRAPH");
    rerender(
      <I18nProvider locale="zh-CN">
        <Messages onAction={action} />
      </I18nProvider>,
    );
    expect(screen.getByText(/仅批准 2/)).toHaveTextContent("sub-001 · sub-002");
    expect(screen.getByText(/native_python/)).toHaveTextContent("禁用");
    expect(screen.getByText(/源数据保持只读/)).toBeVisible();
    expect(screen.getByText("My registered template")).toBeVisible();
    expect(screen.getByText(/已确认重复时间：2/)).toBeVisible();
    expect(screen.getByText("已确认全局信号回归：排除")).toBeVisible();
    expect(container).not.toHaveTextContent("exclude");
    expect(screen.getByText(/图谱记忆建议必须/)).toBeVisible();
    expect(screen.getByText(/诊断标识：unknown.future/)).toBeVisible();
    expect(container).not.toHaveTextContent("SECRET_BACKEND_PARAGRAPH");
    expect(container.querySelector("[data-summary-hash]")).toHaveAttribute(
      "data-summary-hash",
      "actual-unchanged-hash",
    );
    expect(action).not.toHaveBeenCalled();
  });
});
