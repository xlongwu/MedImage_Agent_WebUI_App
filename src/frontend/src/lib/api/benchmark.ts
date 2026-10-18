import { requestJson } from "./client";

export async function detectGpu(baseUrl: string) {
  return requestJson<Record<string, unknown>>("/api/gpu/detect", { baseUrl });
}

export async function runGpuBenchmark(
  baseUrl: string,
  payload: {
    subject_id?: string;
    input_nii?: string;
    derivatives_dir?: string;
    tr?: number;
    freq_band?: number[];
    prefer_gpu?: boolean;
    require_gpu?: boolean;
    benchmark_compare_cpu_gpu?: boolean;
  },
) {
  return requestJson<Record<string, unknown>>("/api/gpu/benchmark", {
    method: "POST",
    body: JSON.stringify(payload),
    baseUrl,
  });
}
