import type { I18nContextValue } from "../../i18n/context";
import { messagesEn, type MessageKey } from "../../i18n/messages/en";
import type { SystemMessage } from "../../lib/types/agentTask";

export function formatSystemMessage(t: I18nContextValue["t"], message: SystemMessage): string {
  const key = `semantic.${message.code}`;
  if (!Object.prototype.hasOwnProperty.call(messagesEn, key))
    return t("semantic.unknown", { code: message.code });
  const params = message.params;
  const enumText = (prefix: string, value: string) => {
    const enumKey = `semantic.${prefix}.${value}`;
    return Object.prototype.hasOwnProperty.call(messagesEn, enumKey)
      ? t(enumKey as MessageKey)
      : t("common.unavailable");
  };
  return t(key as MessageKey, {
    count: params.count ?? 0,
    subject_ids: params.subject_ids?.join(" · ") || t("semantic.scope.all"),
    resource_name: params.resource_name ?? "",
    license: params.license ?? "",
    checksum: params.checksum ?? "",
    source: params.source ? enumText("source", params.source) : "",
    value:
      typeof params.value === "boolean"
        ? enumText("value", String(params.value))
        : params.decision_kind === "global_signal_regression"
          ? enumText("value", String(params.value))
          : params.decision_kind === "repetition_time" &&
              typeof params.value === "string" &&
              ["bids", "project", "dicom"].includes(params.value)
            ? enumText("source", params.value)
            : String(params.value ?? ""),
    decision_kind: params.decision_kind ? enumText("kind", params.decision_kind) : "",
    backend_facts:
      params.backend_facts
        ?.map((item) => `${item.backend_id} (${enumText("status", item.status)})`)
        .join(" · ") ?? "",
    artifact_id: params.artifact_id ?? "",
    template_id: params.template_id ?? "",
    diagnostic_id: params.diagnostic_id ?? "",
  });
}
