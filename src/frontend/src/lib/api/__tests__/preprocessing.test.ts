import { describe, expect, it, vi } from "vitest";

import * as preprocessingApi from "../preprocessing";
import { requestJson } from "../client";

vi.mock("../client", () => ({
  requestJson: vi.fn(),
}));

describe("Preprocessing API", () => {
  it("exposes read-only evidence APIs but not legacy execution wrappers", async () => {
    vi.mocked(requestJson).mockResolvedValueOnce({ run_id: "pp-demo" });

    await preprocessingApi.getLatestNativeFullPreprocessingRun("http://localhost", "project-1");

    expect(requestJson).toHaveBeenCalledWith(
      "/api/projects/project-1/preprocessing/native/runs/latest",
      { baseUrl: "http://localhost" },
    );
    expect(preprocessingApi).not.toHaveProperty("createPreprocessingRun");
    expect(preprocessingApi).not.toHaveProperty("runNativeFullPreprocessingDryRun");
  });
});
