import { fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../../../i18n/I18nProvider";
import { AssistantSheet } from "../AssistantSheet";

function renderSheet(
  overrides: Partial<ComponentProps<typeof AssistantSheet>> = {},
  locale: "en" | "zh-CN" = "en",
) {
  const onInput = vi.fn();
  const onNewChat = vi.fn();
  const onOpenChange = vi.fn();
  const onSubmit = vi.fn();

  render(
    <I18nProvider locale={locale}>
      <AssistantSheet
        activePageLabel="Runs"
        error=""
        input=""
        loading={false}
        messages={[{ role: "assistant", text: "Ready to help with the current workspace." }]}
        onInput={onInput}
        onNewChat={onNewChat}
        onOpenChange={onOpenChange}
        onSubmit={onSubmit}
        open={true}
        projectName="Demo Project"
        selectionContext={{
          artifact: null,
          dataSeries: {
            evidenceLevel: "preview_only",
            series: "series-001",
            seriesDetail: "Series UID",
            sourceKind: "mapping_preview",
            status: "high",
            subject: "sub-001",
            subjectDetail: "dicom_series",
            warnings: [],
          },
          image: {
            plane: "axial",
            series: "bold",
            source: "sub-001/func/sub-001_task-rest_bold.nii.gz",
            subjectId: "sub-001",
          },
          planNode: {
            backend: "spm",
            detail: "Prepare realignment through reviewed backend gates.",
            id: "spm_realign",
            name: "Motion correction",
            risk: "High risk",
          },
          run: {
            id: "task-1",
            name: "Preprocessing run",
            pipeline: "rs-fMRI preprocessing",
            status: "failed",
          },
        }}
        {...overrides}
      />
    </I18nProvider>,
  );

  return { onInput, onNewChat, onOpenChange, onSubmit };
}

describe("AssistantSheet", () => {
  it("shows project context and separates suggestions from execution actions", () => {
    renderSheet();

    expect(screen.getByRole("dialog", { name: "Assistant" })).toBeInTheDocument();
    expect(screen.getByLabelText("Assistant context")).toHaveTextContent("Demo Project");
    expect(screen.getByLabelText("Assistant context")).toHaveTextContent("Runs");
    expect(screen.getByLabelText("Assistant context")).toHaveTextContent("Preprocessing run");
    expect(screen.getByLabelText("Assistant context")).toHaveTextContent(
      "data series sub-001 / series-001 / subject sub-001 / series bold / node Motion correction",
    );
    expect(screen.getByLabelText("Assistant context")).toHaveTextContent(
      "Explain / summarize / draft",
    );
    expect(screen.getByLabelText("Assistant context")).toHaveTextContent(
      "Mock provider: no external API used; real LLM disabled until API key is configured",
    );
    expect(screen.getByLabelText("Assistant suggestions")).toHaveTextContent("Suggested prompts");
    expect(screen.getByLabelText("Assistant suggestions")).toHaveTextContent(
      "Explain the selected run diagnostics",
    );
    expect(screen.getByText("Execution boundary")).toBeInTheDocument();
    expect(screen.getByText(/Mock provider mode uses the local safe default/i)).toBeInTheDocument();
  });

  it("copies suggested prompts into the assistant input", () => {
    const { onInput } = renderSheet();

    fireEvent.click(screen.getByRole("button", { name: "Draft the next reviewed run follow-up" }));

    expect(onInput).toHaveBeenCalledWith("Draft the next reviewed run follow-up");
  });

  it("keeps chat controls available inside the sheet", () => {
    const { onNewChat, onSubmit } = renderSheet({ input: "Explain Runs" });

    fireEvent.click(screen.getByRole("button", { name: "New Chat" }));
    fireEvent.submit(screen.getByRole("textbox", { name: "Ask AI Assistant" }).closest("form")!);

    expect(onNewChat).toHaveBeenCalled();
    expect(onSubmit).toHaveBeenCalled();
  });

  it("renders assistant context and prompts in Chinese", () => {
    renderSheet({}, "zh-CN");

    expect(screen.getByRole("dialog", { name: "助手" })).toBeInTheDocument();
    expect(screen.getByLabelText("助手上下文")).toHaveTextContent("解释／总结／起草");
    expect(screen.getByLabelText("助手建议")).toHaveTextContent("解释所选运行诊断");
    expect(screen.getByRole("button", { name: "新对话" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "向 AI 助手提问" })).toBeInTheDocument();
  });

  it("uses explanation-only task prompts in the Agent workspace", () => {
    renderSheet({ activePageLabel: "Agent" });

    expect(screen.getByLabelText("Assistant suggestions")).toHaveTextContent(
      "Explain what the current Agent task is waiting for",
    );
    expect(screen.getByText("Execution boundary")).toBeInTheDocument();
  });
});
