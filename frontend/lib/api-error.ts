// The backend answers {"error": {"code", "message", "details": [{"loc", "msg", "type"}]}}
// (docs/api.md). `details` never carries the rejected value, so it is safe to show.

export type ParsedApiError = {
  code: string;
  message: string;
  /** Field name to message, for inline errors. Errors about the whole body use "_form". */
  fields: Record<string, string>;
};

const FALLBACK = "Algo deu errado. Tente de novo.";

function cleanMessage(msg: string): string {
  return msg.replace(/^Value error, /, "");
}

export function parseApiError(error: unknown, fallback: string = FALLBACK): ParsedApiError {
  const envelope = (error as { error?: unknown } | null | undefined)?.error;
  if (!envelope || typeof envelope !== "object") {
    return { code: "unknown", message: fallback, fields: {} };
  }
  const { code, message, details } = envelope as {
    code?: unknown;
    message?: unknown;
    details?: unknown;
  };
  const fields: Record<string, string> = {};
  if (Array.isArray(details)) {
    for (const detail of details) {
      const loc = (detail as { loc?: unknown }).loc;
      const msg = (detail as { msg?: unknown }).msg;
      if (!Array.isArray(loc) || typeof msg !== "string") continue;
      // ["body", "link_url"] -> link_url. A bare ["body"] is a rule across fields.
      const field = loc.length > 1 ? String(loc[1]) : "_form";
      fields[field] ??= cleanMessage(msg);
    }
  }
  const text = typeof message === "string" && message ? cleanMessage(message) : fallback;
  return {
    code: typeof code === "string" ? code : "unknown",
    message: fields._form ?? text,
    fields,
  };
}

export class ApiCallError extends Error {
  readonly info: ParsedApiError;
  readonly status: number;
  constructor(info: ParsedApiError, status: number) {
    super(info.message);
    this.name = "ApiCallError";
    this.info = info;
    this.status = status;
  }
}
