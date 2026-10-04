import { afterEach, expect, it, vi } from "vitest";
import { getSandboxAttempt, listSandboxAttempts } from "../sandboxes";
import type { SandboxAttempt } from "../../types/sandbox";

afterEach(() => vi.unstubAllGlobals());

it.each(["unverified", "enforced"] as const)(
  "preserves backend network status %s",
  async (network) => {
    const attempt: SandboxAttempt = {
      sandbox_id: "sandbox",
      run_id: "run",
      node_id: "node",
      subject_id: null,
      status: "PREPARED",
      started_at: null,
      ended_at: null,
      result_code: null,
      output_count: 0,
      policy_version: "windows-sandbox-v2",
      network_isolation: network,
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: async () =>
        JSON.stringify({
          ok: true,
          project_id: "project",
          run_id: "run",
          sandbox_attempts: [attempt],
          sandbox_attempt: attempt,
        }),
    });
    vi.stubGlobal("fetch", fetchMock);
    expect(
      (await listSandboxAttempts("http://api", "project", "run")).sandbox_attempts[0]
        .network_isolation,
    ).toBe(network);
    expect(
      (await getSandboxAttempt("http://api", "project", "run", "sandbox")).sandbox_attempt
        .network_isolation,
    ).toBe(network);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  },
);
