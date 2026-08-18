import { describe, expect, it, vi } from "vitest";

import * as preprocessingApi from "../preprocessing";
import { requestJson } from "../legacyCore";

vi.mock("../legacyCore", () => ({
  requestJson: vi.fn(),
}));

describe("Preprocessing API", () => {
  it("exposes read-only evidence APIs but not legacy execution wrappers", async () => {
    vi.mocked(requestJson).mockResolvedValueOnce({ run_id: "pp-demo" });

    await preprocessingApi.getLatestNativeFullPreprocessingRun("http://localhost", "project-1");

    expect(requestJson).toHaveBeenCalledWith(
      "http://localhost",
      "/api/projects/project-1/preprocessing/native/runs/latest",
    );
    expect(preprocessingApi).not.toHaveProperty("createPreprocessingRun");
    expect(preprocessingApi).not.toHaveProperty("runNativeFullPreprocessingDryRun");
  });
});
