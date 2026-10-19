"use client";

import { useState } from "react";
import useSWR from "swr";
import { PencilIcon, PlusIcon, TrashIcon } from "@/components/icons";
import { Card, ConfirmDialog, IconButton, IconLink, LinkButton, Notice, Pill } from "@/components/ui";
import { ApiCallError } from "@/lib/api-error";
import { browserApi, unwrap } from "@/lib/api.client";
import type { Flow } from "@/lib/chat";
import { messages } from "@/lib/messages";

const t = messages.chat.flows;

export function FlowsView() {
  const { data, error, mutate } = useSWR("chat-flows", () =>
    unwrap(browserApi.GET("/api/v1/chat/flows")),
  );
  const { data: config } = useSWR("chat-config", () => unwrap(browserApi.GET("/api/v1/chat/config")));
  const [removing, setRemoving] = useState<Flow | null>(null);
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const welcome = config?.config.welcome_flow_id ?? null;

  async function remove() {
    if (!removing) return;
    setBusy(true);
    try {
      await unwrap(
        browserApi.DELETE("/api/v1/chat/flows/{flow_id}", {
          params: { path: { flow_id: removing.id } },
        }),
      );
      setFlash({ kind: "ok", text: t.deleted });
      await mutate();
    } catch (e) {
      setFlash({
        kind: "error",
        text: e instanceof ApiCallError ? e.message : messages.common.loadFailed,
      });
    } finally {
      setBusy(false);
      setRemoving(null);
    }
  }

  return (
    <div className="mt-6 space-y-4">
      <div className="flex justify-end">
        <LinkButton href="/chat/flows/new" variant="primary">
          <PlusIcon size={16} />
          {t.new}
        </LinkButton>
      </div>
      {flash ? <Notice kind={flash.kind}>{flash.text}</Notice> : null}
      {error ? <Notice kind="error">{messages.common.loadFailed}</Notice> : null}
      {!data ? (
        <p className="text-sm text-muted">{messages.common.loading}</p>
      ) : data.length === 0 ? (
        <Card className="p-6 text-sm text-muted">{t.empty}</Card>
      ) : (
        <ul className="grid gap-4 md:grid-cols-2">
          {data.map((flow) => (
            <li key={flow.id}>
              <Card className="flex h-full flex-col gap-3 p-4">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate font-semibold">{flow.name}</p>
                    <p className="mt-0.5 text-xs text-muted">
                      {t.steps(flow.steps.length)} · {t.stats(flow.sent, flow.people)}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <IconLink href={`/chat/flows/${flow.id}`} label={t.edit}>
                      <PencilIcon size={16} />
                    </IconLink>
                    <IconButton label={t.delete} onClick={() => setRemoving(flow)}>
                      <TrashIcon size={16} />
                    </IconButton>
                  </div>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  <Pill tone={flow.is_active ? "ok" : "muted"} dot>
                    {flow.is_active ? t.on : t.off}
                  </Pill>
                  {welcome === flow.id ? <Pill tone="accent">{t.welcome}</Pill> : null}
                  {flow.keywords.length === 0 ? (
                    <Pill>{t.noKeywords}</Pill>
                  ) : (
                    flow.keywords.map((k) => <Pill key={k}>{k}</Pill>)
                  )}
                </div>
                <p className="line-clamp-2 text-sm text-muted">
                  {flow.steps[0]?.message.replaceAll("{username}", "@fulano")}
                </p>
              </Card>
            </li>
          ))}
        </ul>
      )}
      <ConfirmDialog
        open={removing !== null}
        title={removing ? t.deleteTitle(removing.name) : ""}
        confirmLabel={t.delete}
        pending={busy}
        onConfirm={remove}
        onCancel={() => setRemoving(null)}
      >
        <p>{t.deleteBody}</p>
      </ConfirmDialog>
    </div>
  );
}
