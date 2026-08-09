import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../../../i18n/I18nProvider";
import { MemorySettingsPanel } from "../MemorySettingsPanel";
import {
  forgetMemoryItem,
  getMemoryConsent,
  listMemoryCandidates,
  listMemoryItems,
  pinMemoryItem,
  reviewMemoryCandidate,
  restoreMemoryItem,
  setMemoryConsent,
  type MemoryConsentStatus,
} from "../../../lib/api";

vi.mock("../../../lib/api", () => ({
  forgetMemoryItem: vi.fn(),
  getMemoryConsent: vi.fn(),
  listMemoryCandidates: vi.fn(),
  listMemoryItems: vi.fn(),
  memoryCommandId: vi.fn(() => "memory-test-command-0001"),
  pinMemoryItem: vi.fn(),
  reviewMemoryCandidate: vi.fn(),
  restoreMemoryItem: vi.fn(),
  setMemoryConsent: vi.fn(),
}));

const consent: MemoryConsentStatus = {
  schema_version: 2 as const,
  project_id: "project-1",
  status: "healthy",
  available: true,
  generation_available: true,
  use_available: true,
  generate_enabled: false,
  use_enabled: false,
  consent_epoch: 0,
  outbox_cutoff_sequence: 0,
  updated_at: null,
  degraded_reason: null,
  retrieval_policy_version: "memory-retrieval-v1",
  store_healthy: true,
  outbox_max_sequence: 0,
  processed_outbox_sequence: 0,
  outbox_lag: 0,
  retry_jobs: 0,
  dead_letter_jobs: 0,
  active_leases: 0,
  expired_leases: 0,
  pending_forget_records: 0,
  last_forget_wal_truncate_at: null,
};

const candidate = {
  candidate_id: "candidate-1",
  kind: "workflow_lesson",
  canonical_key: "workflow_lesson:retry",
  content_text: "Review transient failures before retrying.",
  impact_class: "workflow",
  candidate_version: 1,
  candidate_hash: "candidate-hash",
  source: {
    source_type: "observation",
    source_id: "observation-1",
    source_hash: "source-hash",
    source_ref: "observation:observation-1",
    source_trust_class: "authoritative_structured",
  },
};

const item = {
  memory_id: "memory-1",
  project_id: "project-1",
  kind: "presentation_preference",
  canonical_key: "presentation_preference:language",
  item_version: 1,
  generation: 0,
  status: "active",
  pinned: false,
  revision: {
    revision_id: "revision-1",
    revision_number: 1,
    generation: 0,
    content: { language: "zh-CN" },
    content_text: "Use Chinese reports.",
    content_hash: "revision-hash",
    impact_class: "presentation",
  },
  sources: [
    {
      source_type: "explicit_remember",
      source_id: "command-1",
      source_hash: "source-hash",
      source_ref: "explicit_remember:command-1",
      source_trust_class: "explicit_user",
    },
  ],
};

describe("MemorySettingsPanel", () => {
  beforeEach(() => {
    vi.mocked(getMemoryConsent).mockResolvedValue(consent);
    vi.mocked(listMemoryItems).mockImplementation(async (_projectId, _options, status) => ({
      items: status === "forgotten" ? [] : [item],
      total: status === "forgotten" ? 0 : 1,
      next_cursor: null,
    }));
    vi.mocked(listMemoryCandidates).mockResolvedValue({
      items: [candidate],
      total: 1,
      next_cursor: null,
    });
    vi.mocked(setMemoryConsent).mockResolvedValue({ ...consent, generate_enabled: true });
    vi.mocked(reviewMemoryCandidate).mockResolvedValue({});
    vi.mocked(pinMemoryItem).mockResolvedValue({});
    vi.mocked(restoreMemoryItem).mockResolvedValue({});
    vi.mocked(forgetMemoryItem).mockResolvedValue({});
  });

  it("shows separate generation/use controls and provenance-backed entries", async () => {
    render(
      <I18nProvider locale="en">
        <MemorySettingsPanel baseUrl="http://localhost" projectId="project-1" />
      </I18nProvider>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Loading project memory");
    expect(await screen.findByRole("button", { name: "Enable memory generation" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Enable memory suggestions" })).toBeEnabled();
    expect(screen.getByRole("table", { name: "Active project memory" })).toHaveTextContent(
      "explicit_remember:command-1",
    );
    expect(
      screen.getByRole("table", { name: "Memory candidates awaiting review" }),
    ).toHaveTextContent("workflow");

    fireEvent.click(screen.getByRole("button", { name: "Enable memory generation" }));
    await waitFor(() => expect(setMemoryConsent).toHaveBeenCalled());
  });

  it("requires confirmation before forget and supports candidate review", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(
      <I18nProvider locale="en">
        <MemorySettingsPanel baseUrl="http://localhost" projectId="project-1" />
      </I18nProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Accept" }));
    await waitFor(() => expect(reviewMemoryCandidate).toHaveBeenCalled());
    fireEvent.click(await screen.findByRole("button", { name: "Forget" }));
    await waitFor(() => expect(forgetMemoryItem).toHaveBeenCalled());
    fireEvent.click(await screen.findByRole("button", { name: "Pin" }));
    await waitFor(() => expect(pinMemoryItem).toHaveBeenCalled());
  });

  it("restores a forgotten tombstone only with newly supplied content", async () => {
    const forgotten = { ...item, item_version: 2, status: "forgotten" };
    vi.mocked(listMemoryItems).mockImplementation(async (_projectId, _options, status) => ({
      items: status === "forgotten" ? [forgotten] : [],
      total: 1,
      next_cursor: null,
    }));
    vi.spyOn(window, "prompt")
      .mockReturnValueOnce("Restored preference")
      .mockReturnValueOnce('{"language":"zh-CN"}');
    render(
      <I18nProvider locale="en">
        <MemorySettingsPanel baseUrl="http://localhost" projectId="project-1" />
      </I18nProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Restore with new content" }));
    await waitFor(() =>
      expect(restoreMemoryItem).toHaveBeenCalledWith(
        "project-1",
        forgotten,
        { language: "zh-CN" },
        "Restored preference",
        "memory-test-command-0001",
        { baseUrl: "http://localhost" },
      ),
    );
  });

  it("renders partial health counts and hides restricted content", async () => {
    vi.mocked(getMemoryConsent).mockResolvedValue({
      ...consent,
      status: "partial",
      outbox_lag: 3,
      retry_jobs: 1,
      dead_letter_jobs: 2,
      pending_forget_records: 1,
    });
    vi.mocked(listMemoryItems).mockImplementation(async (_projectId, _options, status) => ({
      items:
        status === "forgotten"
          ? []
          : [{ ...item, revision: { ...item.revision, sensitivity: "restricted" } }],
      total: status === "forgotten" ? 0 : 1,
      next_cursor: null,
    }));
    render(
      <I18nProvider locale="en">
        <MemorySettingsPanel baseUrl="http://localhost" projectId="project-1" />
      </I18nProvider>,
    );

    expect(await screen.findByText("Partially available")).toBeInTheDocument();
    expect(screen.getByText(/Source lag: 3; retries: 1; dead letters: 2/)).toBeInTheDocument();
    expect(screen.getByText("Sensitive content hidden")).toBeInTheDocument();
    expect(screen.queryByText("Use Chinese reports.")).not.toBeInTheDocument();
  });

  it("renders installation-disabled controls and Chinese text", async () => {
    vi.mocked(getMemoryConsent).mockResolvedValue({
      ...consent,
      status: "failure",
      available: false,
      generation_available: false,
      use_available: false,
      degraded_reason: "MEMORY_STORE_UNHEALTHY",
    });
    render(
      <I18nProvider locale="zh-CN">
        <MemorySettingsPanel baseUrl="http://localhost" projectId="project-1" />
      </I18nProvider>,
    );
    expect(await screen.findByText("健康检查失败，当前不可用")).toBeInTheDocument();
    expect(screen.getByText("记忆已启用但当前不可用，请先修复健康问题。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "启用记忆生成" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "启用记忆提示" })).toBeDisabled();
  });
});
