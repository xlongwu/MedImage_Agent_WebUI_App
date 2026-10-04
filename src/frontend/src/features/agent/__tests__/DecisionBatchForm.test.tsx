import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../../../i18n/I18nProvider";
import { DecisionBatchForm } from "../components/DecisionBatchForm";
import type { AgentTaskDecisionBatch } from "../../../lib/types/agentTask";

const batch: AgentTaskDecisionBatch = {
  batch_id: "batch",
  evidence_snapshot_hash: "evidence",
  plan_hash_before: null,
  expires_at: "2027-01-01T00:00:00Z",
  items: [
    {
      item_id: "template",
      kind: "template",
      question: { code: "resource.name", params: { resource_name: "Choose template" } },
      impact: { code: "resource.name", params: { resource_name: "Spatial correspondence" } },
      options: [],
      recommended_option: null,
      answer_type: "option",
      min_value: null,
      max_value: null,
      required: true,
      evidence_refs: [],
      readiness: "input_required",
      allowed_actions: ["register_template"],
    },
  ],
};

describe.each(["en", "zh-CN"] as const)("template input %s", (locale) => {
  it("disables science confirmation and registers only a complete resource", async () => {
    const answer = vi.fn();
    const register = vi.fn().mockResolvedValue(undefined);
    render(
      <I18nProvider locale={locale}>
        <DecisionBatchForm
          batch={batch}
          mutating={false}
          onAnswer={answer}
          onRegisterTemplate={register}
        />
      </I18nProvider>,
    );
    const confirm = screen.getByRole("button", {
      name: locale === "en" ? "Confirm and continue" : "确认并继续",
    });
    expect(confirm).toBeDisabled();
    const registration = screen.getByRole("button", {
      name: locale === "en" ? "Register template and rebuild choices" : "登记模板并重新生成选择",
    });
    expect(registration).toBeDisabled();
    const inputs = screen.getAllByRole("textbox");
    ["fixture", "resources/templates/template.nii.gz", "CC0"].forEach((value, index) =>
      fireEvent.change(inputs[index], { target: { value } }),
    );
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "MNI152" } });
    fireEvent.click(registration);
    expect(register).toHaveBeenCalledWith("batch", {
      name: "fixture",
      path: "resources/templates/template.nii.gz",
      license: "CC0",
      space: "MNI152",
    });
    expect(answer).not.toHaveBeenCalled();
  });

  it("keeps failed registration visible and does not submit science answers", async () => {
    const register = vi.fn().mockRejectedValue(new Error("fixture"));
    render(
      <I18nProvider locale={locale}>
        <DecisionBatchForm
          batch={batch}
          mutating={false}
          onAnswer={vi.fn()}
          onRegisterTemplate={register}
        />
      </I18nProvider>,
    );
    screen
      .getAllByRole("textbox")
      .forEach((input) => fireEvent.change(input, { target: { value: "fixture" } }));
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "MNI152" } });
    fireEvent.click(
      screen.getByRole("button", {
        name: locale === "en" ? "Register template and rebuild choices" : "登记模板并重新生成选择",
      }),
    );
    expect(
      await screen.findByText(
        locale === "en"
          ? "Template registration failed. Check the project path, NIfTI volume, license and declared space."
          : "模板登记失败。请检查项目内路径、NIfTI 数据、许可证和声明空间。",
      ),
    ).toBeVisible();
  });
});
