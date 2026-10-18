import { requestJson } from "./client";

export async function createDpabiTemplateWizardInstance(
  baseUrl: string,
  payload: {
    template_id: string;
    instance_id?: string | null;
    run_id?: string | null;
    function_name: string;
    fwhm: number[];
    subjects: string[];
    scheduler: Record<string, unknown>;
  },
) {
  return requestJson<Record<string, unknown>>("/api/dpabi/template-wizard/create", {
    method: "POST",
    body: JSON.stringify(payload),
    baseUrl,
  });
}

export async function executeDpabiTemplate(
  baseUrl: string,
  payload: {
    project_config_path?: string;
    work_dir?: string;
    log_dir?: string;
    instance_id?: string;
    approved?: boolean;
    approved_by?: string;
  },
) {
  return requestJson<Record<string, unknown>>("/api/dpabi/template-execute", {
    method: "POST",
    body: JSON.stringify(payload),
    baseUrl,
  });
}

export async function generateDpabiSubjectWrapperReport(
  baseUrl: string,
  payload: {
    project_config_path?: string;
  },
) {
  return requestJson<Record<string, unknown>>("/api/dpabi/subject-wrapper-report", {
    method: "POST",
    body: JSON.stringify(payload),
    baseUrl,
  });
}

export async function generateDpabiTemplateLibrary(
  baseUrl: string,
  payload: {
    project_config_path?: string;
    work_dir?: string;
    log_dir?: string;
  },
) {
  return requestJson<Record<string, unknown>>("/api/dpabi/template-library", {
    method: "POST",
    body: JSON.stringify(payload),
    baseUrl,
  });
}

export async function generateDpabiWrapperContracts(
  baseUrl: string,
  payload: {
    project_config_path?: string;
    work_dir?: string;
    log_dir?: string;
  },
) {
  return requestJson<Record<string, unknown>>("/api/dpabi/wrapper-contracts", {
    method: "POST",
    body: JSON.stringify(payload),
    baseUrl,
  });
}

export async function generateDpabiWrapperValidationMatrix(
  baseUrl: string,
  payload: {
    project_config_path?: string;
    work_dir?: string;
    log_dir?: string;
    signatures_path?: string;
    contracts_path?: string;
    sandbox_result_path?: string;
    subject_wrapper_summary_path?: string;
  },
) {
  return requestJson<Record<string, unknown>>("/api/dpabi/wrapper-validation-matrix", {
    method: "POST",
    body: JSON.stringify(payload),
    baseUrl,
  });
}

export async function getDpabiTemplateWizardLatest(baseUrl: string) {
  return requestJson<Record<string, unknown>>("/api/dpabi/template-wizard/latest", { baseUrl });
}

export async function getDpabiTemplateWizardOptions(baseUrl: string) {
  return requestJson<Record<string, unknown>>("/api/dpabi/template-wizard/options", { baseUrl });
}

export async function instantiateDpabiTemplate(
  baseUrl: string,
  payload: {
    project_config_path?: string;
    work_dir?: string;
    log_dir?: string;
    template_id?: string;
    instance_id?: string | null;
    run_id?: string | null;
    function_name?: string | null;
    fwhm?: number[] | null;
    subjects?: string[] | null;
  },
) {
  return requestJson<Record<string, unknown>>("/api/dpabi/template-instantiate", {
    method: "POST",
    body: JSON.stringify(payload),
    baseUrl,
  });
}

export async function listDpabiTemplates(baseUrl: string, workDir: string = "./work") {
  return requestJson<Record<string, unknown>>(
    `/api/dpabi/templates?work_dir=${encodeURIComponent(workDir)}`,
    {
      method: "GET",
      baseUrl,
    },
  );
}

export async function previewDpabiTemplateWizard(
  baseUrl: string,
  payload: {
    template_id: string;
    instance_id?: string | null;
    run_id?: string | null;
    function_name: string;
    fwhm: number[];
    subjects: string[];
    scheduler: Record<string, unknown>;
  },
) {
  return requestJson<Record<string, unknown>>("/api/dpabi/template-wizard/preview", {
    method: "POST",
    body: JSON.stringify(payload),
    baseUrl,
  });
}

export async function runDpabiCapability(
  baseUrl: string,
  payload: {
    project_config_path?: string;
    work_dir?: string;
    log_dir?: string;
  },
) {
  return requestJson<Record<string, unknown>>("/api/dpabi/capability", {
    method: "POST",
    body: JSON.stringify(payload),
    baseUrl,
  });
}

export async function runDpabiInputManifest(
  baseUrl: string,
  payload: {
    project_config_path?: string;
    work_dir?: string;
    log_dir?: string;
    dataset_index?: string;
  },
) {
  return requestJson<Record<string, unknown>>("/api/dpabi/input-manifest", {
    method: "POST",
    body: JSON.stringify(payload),
    baseUrl,
  });
}

export async function runDpabiPreflight(
  baseUrl: string,
  payload: {
    project_config_path?: string;
    work_dir?: string;
    log_dir?: string;
    dataset_index?: string;
    capabilities_path?: string;
    manifest_path?: string;
    wrapper_config_template_path?: string;
  },
) {
  return requestJson<Record<string, unknown>>("/api/dpabi/preflight", {
    method: "POST",
    body: JSON.stringify(payload),
    baseUrl,
  });
}

export async function runDpabiRunPlan(
  baseUrl: string,
  payload: {
    project_config_path?: string;
    work_dir?: string;
    log_dir?: string;
    capabilities_path?: string;
    manifest_path?: string;
    preflight_path?: string;
    params_path?: string;
  },
) {
  return requestJson<Record<string, unknown>>("/api/dpabi/run-plan", {
    method: "POST",
    body: JSON.stringify(payload),
    baseUrl,
  });
}

export async function runDpabiSandboxSmoke(
  baseUrl: string,
  payload: {
    project_config_path?: string;
    work_dir?: string;
    log_dir?: string;
    approved?: boolean;
    approved_by?: string;
  },
) {
  return requestJson<Record<string, unknown>>("/api/dpabi/sandbox-smoke", {
    method: "POST",
    body: JSON.stringify(payload),
    baseUrl,
  });
}

export async function runDpabiScaffold(
  baseUrl: string,
  payload: {
    project_config_path?: string;
    work_dir?: string;
    log_dir?: string;
  },
) {
  return requestJson<Record<string, unknown>>("/api/dpabi/scaffold", {
    method: "POST",
    body: JSON.stringify(payload),
    baseUrl,
  });
}

export async function runDpabiSignatureProbe(
  baseUrl: string,
  payload: {
    project_config_path?: string;
    work_dir?: string;
    log_dir?: string;
  },
) {
  return requestJson<Record<string, unknown>>("/api/dpabi/signature-probe", {
    method: "POST",
    body: JSON.stringify(payload),
    baseUrl,
  });
}

export async function runDpabiSingleFunctionSandbox(
  baseUrl: string,
  payload: {
    project_config_path?: string;
    work_dir?: string;
    log_dir?: string;
    function_name?: string;
    approved?: boolean;
    approved_by?: string;
  },
) {
  return requestJson<Record<string, unknown>>("/api/dpabi/single-function-sandbox", {
    method: "POST",
    body: JSON.stringify(payload),
    baseUrl,
  });
}

export async function runDpabiSubjectSmooth(
  baseUrl: string,
  payload: {
    project_config_path?: string;
    work_dir?: string;
    log_dir?: string;
    subject_id?: string;
    input_bold?: string;
    function_name?: string;
    fwhm?: number[];
    approved?: boolean;
  },
) {
  return requestJson<Record<string, unknown>>("/api/dpabi/subject-smooth", {
    method: "POST",
    body: JSON.stringify(payload),
    baseUrl,
  });
}
