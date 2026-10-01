# M11 Contract Schema Consistency Review

> M11-GUI-MODEL-CONTRACT-STABILIZE-T001 | Cross-contract schema consistency  
> Status: COMPLETE | Date: 2026-07-11  
> Covers: T001–T005 contract modules

---

## 1. Executive Summary

This document reviews the five M11 contract modules for schema consistency, verifying that result dataclasses, status codes, error codes, permission flags, and fail-closed behavior align across the provider policy, runtime isolation, source policy, input redaction, and audit metadata contracts.

**Findings:** All five contracts share the same core schema pattern. No permission leak detected. One naming variation noted (source uses `model_load_allowed`; others don't have this field — semantically correct). All blocked results fail closed. All allowed results do not grant execution permission.

---

## 2. Reviewed Contract Surfaces

| Contract | Result Dataclass | Key Fields |
|------|------|------|
| Provider | `ModelProviderPolicyResult` | ok, status, error_code, provider_call_allowed, inference_allowed, model_loaded, network_accessed |
| Runtime | `ModelRuntimeIsolationResult` | ok, status, error_code, provider_call_allowed, inference_allowed, runtime_started, model_loaded, network_accessed, gpu_accessed |
| Source | `ModelSourcePolicyResult` | ok, status, error_code, provider_call_allowed, model_load_allowed, model_loaded, filesystem_accessed, network_accessed |
| Input | `ModelInputRedactionResult` | ok, status, error_code, provider_call_allowed, inference_allowed, model_called, network_accessed, prompt_envelope |
| Audit | `ModelAuditContractResult` | ok, status, error_code, provider_call_allowed, audit_write_allowed, audit_written, + 5 safety flags |

---

## 3. Canonical Common Result Fields

Every contract result dataclass includes:

| Field | Type | Semantics |
|------|------|------|
| `ok` | `bool` | Contract passed validation |
| `status` | `str` | `MODEL_*_ALLOWED` or `MODEL_*_BLOCKED` |
| `error_code` | `str\|None` | Specific rejection reason |
| `provider_call_allowed` | `bool` | **Always `False`** — contracts never grant GUI provider permission |
| `message` | `str` | Human-readable detail |

---

## 4. Contract-Specific Fields

| Field | Provider | Runtime | Source | Input | Audit |
|------|:---:|:---:|:---:|:---:|:---:|
| `inference_allowed` | ✅ | ✅ | — | ✅ | ✅ |
| `model_loaded` | ✅ | ✅ | ✅ | — | — |
| `model_called` | — | — | — | ✅ | ✅ |
| `model_load_allowed` | — | — | ✅ | — | — |
| `runtime_started` | — | ✅ | — | — | — |
| `network_accessed` | ✅ | ✅ | ✅ | ✅ | — |
| `gpu_accessed` | — | ✅ | — | — | — |
| `filesystem_accessed` | — | — | ✅ | — | — |
| `prompt_envelope` | — | — | — | ✅ | — |
| `audit_record` | — | — | — | — | ✅ |
| `audit_path` | — | — | — | — | ✅ |
| `audit_write_allowed` | — | — | — | — | ✅ |
| `audit_written` | — | — | — | — | ✅ |
| `submitted_to_guard` | — | — | — | — | ✅ |
| `desktop_touched` | — | — | — | — | ✅ |
| `screenshot_captured` | — | — | — | — | ✅ |
| `clipboard_accessed` | — | — | — | — | ✅ |
| `mouse_used` | — | — | — | — | ✅ |
| `keyboard_used` | — | — | — | — | ✅ |

---

## 5. Status Taxonomy

| Contract | Allowed | Blocked |
|------|------|------|
| Provider | `MODEL_PROVIDER_ALLOWED` | `MODEL_PROVIDER_BLOCKED` |
| Runtime | `MODEL_RUNTIME_ALLOWED` | `MODEL_RUNTIME_BLOCKED` |
| Source | `MODEL_SOURCE_ALLOWED` | `MODEL_SOURCE_BLOCKED` |
| Input | `MODEL_INPUT_ALLOWED` | `MODEL_INPUT_BLOCKED` |
| Audit | `MODEL_AUDIT_ALLOWED` | `MODEL_AUDIT_BLOCKED` |

**Consistent pattern:** `MODEL_<CONTRACT>_ALLOWED` / `MODEL_<CONTRACT>_BLOCKED`. Status implies metadata validation only — no execution authority.

---

## 6. Permission Flag Semantics

| Flag | Value (all contracts, current phase) | Set By |
|------|------|------|
| `provider_call_allowed` | **Always `False`** | Each contract independently |
| `inference_allowed` | **Always `False`** | Provider, Runtime, Input, Audit |
| `model_loaded` | **Always `False`** | Provider, Runtime, Source |
| `model_called` | **Always `False`** | Input, Audit |
| `model_load_allowed` | **Always `False`** | Source |
| `runtime_started` | **Always `False`** | Runtime |
| `network_accessed` | **Always `False`** | Provider, Runtime, Source, Input |
| `gpu_accessed` | **Always `False`** | Runtime |
| `filesystem_accessed` | **Always `False`** | Source |
| `audit_written` | **Always `False`** | Audit |
| `audit_write_allowed` | **`True` only on audit pass** | Audit |

**Key:** The audit contract is the only contract where an "allowed" result has a "true" write-permission field — and even then, `audit_written` is always `false` because no file is written.

---

## 7. Fail-Closed Schema Requirements (all passed)

| Requirement | Status |
|------|:---:|
| Blocked `ok` = `False` | ✅ |
| Blocked `provider_call_allowed` = `False` | ✅ |
| Blocked `inference_allowed` = `False` (where present) | ✅ |
| Blocked `model_loaded` = `False` (where present) | ✅ |
| Blocked `model_called` = `False` (where present) | ✅ |
| Blocked `network_accessed` = `False` (where present) | ✅ |
| Blocked `gpu_accessed` = `False` (runtime) | ✅ |
| Blocked `prompt_envelope` = `None` (input) | ✅ |
| Blocked `audit_record` = `None` (audit) | ✅ |
| Blocked `audit_write_allowed` = `False` (audit) | ✅ |
| Blocked safety flags all `False` (audit) | ✅ |

---

## 8. Allowed-Result Schema Requirements (all passed)

| Requirement | Status |
|------|:---:|
| `ok` = `True` | ✅ |
| `status` ends with `_ALLOWED` | ✅ |
| `error_code` = `None` | ✅ |
| `provider_call_allowed` = `False` | ✅ |
| No real execution permission granted | ✅ |
| No model inference permission granted | ✅ |
| No GUI permission granted | ✅ |

---

## 9. Sensitive-Data Field Policy

| Contract | Blocks | Mechanism |
|------|------|------|
| Input | raw_screenshot, clipboard, credentials, PHI, paths, injection | Pattern scanning + raw input checks |
| Audit | raw_text, raw_json, chain_of_thought, screenshot_bytes, credentials, paths | Recursive forbidden-key scan |
| Source | rawdata, derivatives in model_dir/weights_file | Pure-string path validation |
| Provider, Runtime | (No sensitive data in scope) | — |

---

## 10. Inconsistency Findings

| # | Finding | Severity |
|:---:|------|:---:|
| 1 | Source contract uses `model_load_allowed` while others use `model_loaded`/`model_called` | ✅ Accepted — distinct semantics (load permission vs load state vs call state) |
| 2 | Audit contract has extra fields not present in other contracts (`submitted_to_guard`, `desktop_touched`, `screenshot_captured`, `clipboard_accessed`, `mouse_used`, `keyboard_used`) | ✅ Accepted — audit is the final checkpoint; these fields are essential for that role |
| 3 | Input contract has `model_called` while provider/runtime have `model_loaded` | ✅ Accepted — "called" (inference invoked) vs "loaded" (weights in memory) are distinct semantics |
| 4 | Runtime has `gpu_accessed`, 其他都没有 | ✅ Accepted — only runtime manages GPU |

**Assessment:** All differences are intentional design choices with distinct semantics. No inconsistency affects safety.

---

## 11. Side-Effect-Free Contract Policy

| Check | Status |
|------|:---:|
| All 5 contract modules do not import `pywinauto` | ✅ |
| All 5 contract modules do not import `torch` | ✅ |
| All 5 contract modules do not import `transformers` | ✅ |
| All 5 contract modules do not import `safetensors` | ✅ |
| Importing all 5 modules causes no side effects | ✅ |
| All canonical declarations are JSON serializable | ✅ |

---

## 12. Required Regression Tests

See `tests/unit/test_gui_model_contract_schema_consistency.py` — 53 tests covering common fields, status taxonomy, permission flags, fail-closed, allowed-result, sensitive fields, side-effect policy, and regression.

---

## 13. Future Compatibility Notes

| Aspect | Current | Future |
|--------|---------|------|
| `provider_call_allowed` | Always false | Remains false in contracts; only GUI guard sets true |
| `inference_allowed` | Always false | Would become true only in a future implementation phase |
| `model_loaded` | Always false | Would become true only with real model loading behind source policy |
| New contract added | — | Must follow the same canonical schema pattern |
