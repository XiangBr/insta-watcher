"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import useSWR from "swr";
import { SearchIcon, SendIcon, UserIcon } from "@/components/icons";
import { Button, Card, Notice, Pill, inputClass, selectClass } from "@/components/ui";
import { ApiCallError } from "@/lib/api-error";
import { browserApi, unwrap } from "@/lib/api.client";
import type { components } from "@/lib/api-types";
import { inHumanMode, isOurs, windowHoursLeft, type ChatMessage, type Contact } from "@/lib/chat";
import { dateTime, relativeTime, timeUntil } from "@/lib/format";
import { messages } from "@/lib/messages";

const t = messages.chat;
const i = t.inbox;
type Detail = components["schemas"]["ContactDetail"];
type Flash = { kind: "ok" | "error"; text: string } | null;

function errorText(error: unknown): string {
  return error instanceof ApiCallError ? error.message : messages.common.loadFailed;
}

function Kpi({ label, value }: { label: string; value: string | number }) {
  return (
    <Card className="p-3">
      <p className="text-xs text-muted">{label}</p>
      <p className="mt-0.5 text-xl font-bold tabular-nums">{value}</p>
    </Card>
  );
}

function name(contact: Contact): string {
  return contact.username ? `@${contact.username}` : i.unknown;
}

function ContactRow({
  contact,
  selected,
  onSelect,
}: {
  contact: Contact;
  selected: boolean;
  onSelect: () => void;
}) {
  const human = inHumanMode(contact);
  const open = windowHoursLeft(contact) !== null;
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-current={selected ? "true" : undefined}
        className={`flex w-full items-start gap-3 rounded-lg px-3 py-2.5 text-left transition-colors ${
          selected ? "bg-accent-soft" : "hover:bg-surface-2"
        }`}
      >
        <span className="relative grid size-9 shrink-0 place-items-center rounded-full bg-surface-2 text-muted">
          <UserIcon size={18} />
          {open ? (
            <span aria-hidden className="absolute -right-0.5 -bottom-0.5 size-3 rounded-full border-2 border-surface bg-ok" />
          ) : null}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline justify-between gap-2">
            <span className="truncate text-sm font-semibold">{name(contact)}</span>
            <span className="shrink-0 text-xs text-muted">
              {relativeTime(contact.last_inbound_at ?? contact.first_seen_at)}
            </span>
          </span>
          <span className="mt-0.5 block truncate text-xs text-muted">
            {contact.last_source && contact.last_source !== "USER"
              ? `${i.sources[contact.last_source] ?? ""}: `
              : ""}
            {contact.last_text ?? i.noText}
          </span>
          {human || contact.tags.length > 0 ? (
            <span className="mt-1 flex flex-wrap gap-1">
              {human ? <Pill tone="wait">{t.summary.human}</Pill> : null}
              {contact.tags.slice(0, 3).map((tag) => (
                <Pill key={tag}>{tag}</Pill>
              ))}
            </span>
          ) : null}
        </span>
      </button>
    </li>
  );
}

function Bubble({ message }: { message: ChatMessage }) {
  const ours = isOurs(message);
  const source = i.sources[message.source] ?? message.source;
  const tapped = message.direction === "IN" && message.payload !== null;
  return (
    <li className={`flex ${ours ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-[80%] ${ours ? "items-end" : "items-start"} flex flex-col gap-1`}>
        <div
          className={`rounded-2xl px-3.5 py-2 text-sm whitespace-pre-line break-words ${
            ours
              ? message.source === "OWNER"
                ? "bg-fg text-surface"
                : "bg-accent text-accent-fg"
              : "bg-surface-2"
          }`}
        >
          {tapped ? (
            <span className="text-xs opacity-80">{i.tapped(message.text ?? "")}</span>
          ) : (
            (message.text ?? i.noText)
          )}
        </div>
        <span className="text-[11px] text-muted">
          {ours && source ? `${source} · ` : ""}
          {dateTime(message.created_at)}
          {message.cost_usd != null ? ` · ${messages.competitors.usd(message.cost_usd)}` : ""}
        </span>
      </div>
    </li>
  );
}

function Conversation({ contactId, onChanged }: { contactId: number; onChanged: () => void }) {
  const { data, error, mutate } = useSWR(
    ["chat-contact", contactId],
    () =>
      unwrap(
        browserApi.GET("/api/v1/chat/contacts/{contact_id}", {
          params: { path: { contact_id: contactId } },
        }),
      ),
    { refreshInterval: 5_000, revalidateOnFocus: true },
  );
  const [text, setText] = useState("");
  const [tags, setTags] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<Flash>(null);
  const end = useRef<HTMLLIElement>(null);
  const count = data?.messages.length ?? 0;

  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [count, contactId]);

  async function run(action: () => Promise<Detail>, ok: string) {
    setBusy(true);
    setFlash(null);
    try {
      await mutate(await action(), { revalidate: false });
      setFlash({ kind: "ok", text: ok });
      onChanged();
      return true;
    } catch (e) {
      setFlash({ kind: "error", text: errorText(e) });
      return false;
    } finally {
      setBusy(false);
    }
  }

  const path = { params: { path: { contact_id: contactId } } };
  const patch = (body: components["schemas"]["ContactPatch"], ok: string) =>
    run(() => unwrap(browserApi.PATCH("/api/v1/chat/contacts/{contact_id}", { ...path, body })), ok);

  async function send(event: FormEvent) {
    event.preventDefault();
    if (!text.trim()) return;
    const done = await run(
      () =>
        unwrap(
          browserApi.POST("/api/v1/chat/contacts/{contact_id}/messages", {
            ...path,
            body: { text },
          }),
        ),
      i.replyQueued,
    );
    if (done) setText("");
  }

  async function cancel(outboxId: number) {
    setBusy(true);
    try {
      await unwrap(
        browserApi.DELETE("/api/v1/chat/outbox/{outbox_id}", {
          params: { path: { outbox_id: outboxId } },
        }),
      );
      setFlash({ kind: "ok", text: i.cancelled });
      await mutate();
    } catch (e) {
      setFlash({ kind: "error", text: errorText(e) });
    } finally {
      setBusy(false);
    }
  }

  if (error) return <Notice kind="error">{errorText(error)}</Notice>;
  if (!data) return <p className="p-6 text-sm text-muted">{messages.common.loading}</p>;
  const contact = data.contact;
  const human = inHumanMode(contact);
  const hours = windowHoursLeft(contact);

  return (
    <div className="flex h-full min-h-[32rem] flex-col">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line p-4">
        <div>
          <p className="font-semibold">{name(contact)}</p>
          <p className="text-xs text-muted">
            {hours === null ? i.windowClosed : i.windowLeft(hours)}
            {human && contact.human_until
              ? ` · ${i.humanUntil(dateTime(contact.human_until))} (${
                  i.humanReason[contact.human_reason ?? ""] ?? contact.human_reason
                })`
              : ""}
          </p>
        </div>
        <Button
          variant={human ? "primary" : "secondary"}
          disabled={busy}
          onClick={() => patch({ human: !human }, human ? i.resumed : i.paused)}
        >
          {human ? i.resume : i.pause}
        </Button>
      </div>

      <ul className="max-h-[28rem] flex-1 space-y-3 overflow-y-auto p-4">
        {data.messages.map((m) => (
          <Bubble key={m.id} message={m} />
        ))}
        <li ref={end} aria-hidden />
      </ul>

      {data.scheduled.length > 0 ? (
        <div className="border-t border-line px-4 py-3">
          <p className="text-xs font-semibold tracking-wide text-muted uppercase">{i.scheduled}</p>
          <ul className="mt-2 space-y-1.5">
            {data.scheduled.map((o) => (
              <li key={o.id} className="flex items-center justify-between gap-2 text-sm">
                <span className="min-w-0 truncate">
                  <span className="text-muted">{i.kinds[o.kind] ?? o.kind} · {timeUntil(o.run_at)}</span>
                  {o.preview ? ` — ${o.preview}` : ""}
                </span>
                {o.status === "PENDING" ? (
                  <Button variant="ghost" disabled={busy} onClick={() => cancel(o.id)}>
                    {i.cancel}
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {data.problems.length > 0 ? (
        <details className="border-t border-line px-4 py-3 text-sm">
          <summary className="cursor-pointer font-semibold text-fail">{i.problems}</summary>
          <ul className="mt-2 space-y-1 text-muted">
            {data.problems.map((o) => (
              <li key={o.id}>
                {i.kinds[o.kind] ?? o.kind} · {dateTime(o.created_at)}:{" "}
                {o.status === "UNCONFIRMED" ? i.unconfirmed : o.error}
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      <div className="space-y-3 border-t border-line p-4">
        {flash ? <Notice kind={flash.kind}>{flash.text}</Notice> : null}
        <form onSubmit={send} className="flex gap-2">
          <label className="flex-1">
            <span className="sr-only">{i.reply}</span>
            <input
              value={text}
              onChange={(e) => setText(e.target.value)}
              maxLength={1000}
              disabled={hours === null}
              placeholder={hours === null ? i.replyClosed : i.replyPlaceholder}
              className={inputClass}
            />
          </label>
          <Button type="submit" variant="primary" disabled={busy || hours === null || !text.trim()}>
            <SendIcon size={16} />
            {busy ? i.replying : i.reply}
          </Button>
        </form>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void patch({ tags: (tags ?? contact.tags.join(", ")).split(",") }, i.tagsSaved).then(
              (ok) => ok && setTags(null),
            );
          }}
          className="flex gap-2"
        >
          <label className="flex-1">
            <span className="sr-only">{i.tags}</span>
            <input
              value={tags ?? contact.tags.join(", ")}
              onChange={(e) => setTags(e.target.value)}
              placeholder={`${i.tags}: ${i.tagsPlaceholder}`}
              className={inputClass}
            />
          </label>
          <Button type="submit" disabled={busy || tags === null}>
            {i.saveTags}
          </Button>
        </form>
      </div>
    </div>
  );
}

export function InboxView() {
  const [search, setSearch] = useState("");
  const [view, setView] = useState<"all" | "open" | "human">("all");
  const [tag, setTag] = useState("");
  const [selected, setSelected] = useState<number | null>(null);
  const { data, error, mutate } = useSWR(
    ["chat-contacts", search, view, tag],
    () =>
      unwrap(
        browserApi.GET("/api/v1/chat/contacts", {
          params: { query: { search: search || undefined, view, tag: tag || undefined } },
        }),
      ),
    { refreshInterval: 10_000, keepPreviousData: true },
  );
  const s = data?.summary;
  const current = selected ?? data?.contacts[0]?.id ?? null;

  return (
    <div className="mt-6 space-y-4">
      {s ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
          <Kpi label={t.summary.contacts} value={s.contacts} />
          <Kpi label={t.summary.open} value={s.open_windows} />
          <Kpi label={t.summary.human} value={s.in_human_mode} />
          <Kpi label={t.summary.ai} value={s.ai_replies_24h} />
          <Kpi label={t.summary.scheduled} value={s.scheduled} />
          <Kpi label={t.summary.failed} value={s.failed_24h} />
        </div>
      ) : null}
      {error ? <Notice kind="error">{errorText(error)}</Notice> : null}
      <div className="grid gap-4 lg:grid-cols-[22rem_minmax(0,1fr)]">
        <Card className="p-3">
          <div className="space-y-2">
            <label className="relative block">
              <span className="sr-only">{i.search}</span>
              <SearchIcon size={16} className="absolute top-1/2 left-3 -translate-y-1/2 text-muted" />
              <input
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={i.search}
                className={`${inputClass} pl-9`}
              />
            </label>
            <div className="flex gap-2">
              <select
                value={view}
                onChange={(e) => setView(e.target.value as typeof view)}
                className={`${selectClass} flex-1`}
                aria-label={t.tabs.inbox}
              >
                {Object.entries(i.views).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
              <select
                value={tag}
                onChange={(e) => setTag(e.target.value)}
                className={`${selectClass} flex-1`}
                aria-label={i.tags}
              >
                <option value="">{i.allTags}</option>
                {(data?.tags ?? []).map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </div>
          </div>
          {!data ? (
            <p className="p-3 text-sm text-muted">{messages.common.loading}</p>
          ) : data.contacts.length === 0 ? (
            <p className="p-3 text-sm text-muted">
              {search || tag || view !== "all" ? i.emptyFilter : i.empty}
            </p>
          ) : (
            <ul className="mt-2 max-h-[36rem] space-y-0.5 overflow-y-auto">
              {data.contacts.map((c) => (
                <ContactRow
                  key={c.id}
                  contact={c}
                  selected={c.id === current}
                  onSelect={() => setSelected(c.id)}
                />
              ))}
            </ul>
          )}
        </Card>
        <Card className="overflow-hidden">
          {current === null ? (
            <p className="p-6 text-sm text-muted">{i.pick}</p>
          ) : (
            <Conversation key={current} contactId={current} onChanged={() => void mutate()} />
          )}
        </Card>
      </div>
    </div>
  );
}
