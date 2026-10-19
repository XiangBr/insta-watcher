import type { components } from "@/lib/api-types";
import { messages } from "@/lib/messages";

export type Delivery = components["schemas"]["DeliveryOut"];

const t = messages.deliveries;

/**
 * What the list row says about the DM. Without a gate that is the DM's own status. With a
 * gate the first DM is only an invite, so "Enviada" would be misleading: the person has not
 * got the link yet. The row follows the delivery to the end instead.
 */
export function dmSummary(d: Delivery): { status: string; label: string } {
  const plain = { status: d.status, label: t.status[d.status] ?? d.status };
  if (d.gate_state === null || d.gate_state === undefined || d.status !== "SENT") return plain;

  switch (d.followup_status) {
    case "SENT":
      return { status: "SENT", label: t.gate.linkSent };
    case "FAILED":
      return { status: "FAILED", label: t.gate.linkFailed };
    case "UNCONFIRMED":
      return { status: "UNCONFIRMED", label: t.gate.linkUnconfirmed };
    case "PENDING":
      return { status: "PENDING", label: t.gate.linkSending };
  }
  switch (d.gate_state) {
    case "WAITING":
      return { status: "PENDING", label: t.gate.waiting };
    case "CHECKING":
      return { status: "PENDING", label: t.gate.checking };
    case "REMINDED":
      return { status: "PENDING", label: t.gate.reminded };
    case "STOPPED":
      return { status: "FAILED", label: t.gate.stopped };
    default:
      return { status: "PENDING", label: t.gate.releasing };
  }
}

const STATE_TONE: Record<string, string> = {
  WAITING: "bg-wait",
  CHECKING: "bg-wait",
  REMINDED: "bg-wait",
  PASSED: "bg-ok",
  RELEASED: "bg-ok",
  STOPPED: "bg-fail",
};

/** Colour of the marker of the gate step in the trail. */
export function gateTone(state: string): string {
  return STATE_TONE[state] ?? "bg-muted";
}
