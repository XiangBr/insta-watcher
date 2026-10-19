"use client";

import { useState, type FormEvent } from "react";
import useSWR from "swr";
import { SparkIcon } from "@/components/icons";
import { Button, Card, Field, Notice, ToggleCard, inputClass, selectClass } from "@/components/ui";
import { ApiCallError } from "@/lib/api-error";
import { browserApi, unwrap } from "@/lib/api.client";
import type { components } from "@/lib/api-types";
import type { ChatConfig } from "@/lib/chat";
import { messages } from "@/lib/messages";

const t = messages.chat.ai;
const usd = messages.competitors.usd;
type Flash = { kind: "ok" | "error"; text: string } | null;
type TestResult = components["schemas"]["AiTestOut"];

function errorText(error: unknown): string {
  return error instanceof ApiCallError ? error.message : messages.common.loadFailed;
}

function Number_({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (v: number) => void;
}) {
  return (
    <Field label={label}>
      {(p) => (
        <input
          {...p}
          type="number"
          min={min}
          max={max}
          value={value}
          onChange={(e) => onChange(Math.max(min, Math.min(max, Number(e.target.value) || min)))}
          className={`${inputClass} max-w-32`}
        />
      )}
    </Field>
  );
}

function TestBox({ config }: { config: ChatConfig }) {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<TestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run(event: FormEvent) {
    event.preventDefault();
    if (!message.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await unwrap(browserApi.POST("/api/v1/chat/ai/test", { body: { message, config } })));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="space-y-3 p-4">
      <div>
        <p className="font-semibold">{t.test}</p>
        <p className="text-sm text-muted">{t.testHint}</p>
      </div>
      <form onSubmit={run} className="flex gap-2">
        <label className="flex-1">
          <span className="sr-only">{t.test}</span>
          <input
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            maxLength={1000}
            placeholder={t.testPlaceholder}
            className={inputClass}
          />
        </label>
        <Button type="submit" variant="primary" disabled={busy || !message.trim()}>
          <SparkIcon size={16} className={busy ? "animate-pulse" : ""} />
          {busy ? t.testing : t.testRun}
        </Button>
      </form>
      {error ? <Notice kind="error">{error}</Notice> : null}
      {result ? (
        <div className="space-y-2 rounded-2xl bg-neutral-950 p-4 text-sm text-white">
          <div className="flex justify-end">
            <span className="max-w-[85%] rounded-2xl bg-violet-600 px-3 py-2">{message}</span>
          </div>
          <p className="text-xs text-neutral-400">{result.handoff ? t.testHandoff : t.testReply}</p>
          <div className="flex justify-start">
            <span className="max-w-[85%] rounded-2xl bg-neutral-800 px-3 py-2 whitespace-pre-line">
              {result.sent_text}
            </span>
          </div>
          <p className="text-xs text-neutral-500">{t.testCost(result.model, usd(result.cost_usd))}</p>
        </div>
      ) : null}
    </Card>
  );
}

function Form({
  initial,
  available,
  defaultModel,
  usage,
  onSaved,
}: {
  initial: ChatConfig;
  available: boolean;
  defaultModel: string;
  usage: components["schemas"]["ChatUsage"];
  onSaved: () => void;
}) {
  const [config, setConfig] = useState<ChatConfig>(initial);
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<Flash>(null);
  const { data: flows } = useSWR("chat-flows", () => unwrap(browserApi.GET("/api/v1/chat/flows")));
  const set = <K extends keyof ChatConfig>(key: K, value: ChatConfig[K]) => setConfig({ ...config, [key]: value });

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setFlash(null);
    try {
      await unwrap(browserApi.PUT("/api/v1/chat/config", { body: config }));
      setFlash({ kind: "ok", text: t.saved });
      onSaved();
    } catch (e) {
      setFlash({ kind: "error", text: errorText(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,40rem)_minmax(0,1fr)]">
      <form onSubmit={save} className="space-y-4">
        <Card className="space-y-5 p-4">
          <p className="text-sm text-muted">{t.intro}</p>
          {!available ? (
            <p className="rounded-lg bg-wait-soft px-4 py-3 text-sm text-wait">{t.noKey}</p>
          ) : null}
          <ToggleCard
            title={t.enabled}
            description={t.enabledHint}
            checked={Boolean(config.ai_enabled)}
            onChange={(on) => set("ai_enabled", on)}
          />
          <p className="text-xs text-muted">
            {t.usage(usage.ai_replies_24h, usd(usage.ai_cost_24h_usd), usage.handoffs_24h)}
          </p>
          <Field label={t.model} hint={t.modelHint(defaultModel)}>
            {(p) => (
              <input
                {...p}
                value={config.ai_model ?? ""}
                onChange={(e) => set("ai_model", e.target.value || null)}
                placeholder={defaultModel}
                className={inputClass}
              />
            )}
          </Field>
          <Field label={t.persona} hint={t.personaHint}>
            {(p) => (
              <textarea
                {...p}
                rows={3}
                maxLength={2000}
                value={config.persona ?? ""}
                onChange={(e) => set("persona", e.target.value)}
                className={inputClass}
              />
            )}
          </Field>
          <Field label={t.knowledge} hint={t.knowledgeHint}>
            {(p) => (
              <textarea
                {...p}
                rows={10}
                maxLength={20000}
                value={config.knowledge ?? ""}
                onChange={(e) => set("knowledge", e.target.value)}
                className={`${inputClass} font-mono text-xs`}
              />
            )}
          </Field>
          <Field label={t.handoff}>
            {(p) => (
              <textarea
                {...p}
                rows={2}
                maxLength={1000}
                value={config.handoff_message ?? ""}
                onChange={(e) => set("handoff_message", e.target.value)}
                className={inputClass}
              />
            )}
          </Field>
          <div className="grid gap-4 sm:grid-cols-3">
            <Number_
              label={t.handoffHours}
              value={config.handoff_hours ?? 12}
              min={1}
              max={168}
              onChange={(v) => set("handoff_hours", v)}
            />
            <Number_
              label={t.perContact}
              value={config.ai_max_per_contact_day ?? 10}
              min={1}
              max={100}
              onChange={(v) => set("ai_max_per_contact_day", v)}
            />
            <Number_
              label={t.perHour}
              value={config.ai_max_per_hour ?? 60}
              min={1}
              max={1000}
              onChange={(v) => set("ai_max_per_hour", v)}
            />
          </div>
          <p className="text-xs text-muted">{t.limitsHint}</p>
          <Field label={t.welcome} hint={t.welcomeHint}>
            {(p) => (
              <select
                {...p}
                value={config.welcome_flow_id ?? ""}
                onChange={(e) => set("welcome_flow_id", e.target.value ? Number(e.target.value) : null)}
                className={selectClass}
              >
                <option value="">{t.noWelcome}</option>
                {(flows ?? []).map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.name}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <ToggleCard
            title={t.pauseOnManual}
            description={t.pauseOnManualHint}
            checked={config.pause_on_manual_reply ?? true}
            onChange={(on) => set("pause_on_manual_reply", on)}
          />
        </Card>
        {flash ? <Notice kind={flash.kind}>{flash.text}</Notice> : null}
        <Button type="submit" variant="primary" disabled={busy}>
          {busy ? t.saving : t.save}
        </Button>
      </form>
      <div className="lg:sticky lg:top-6">{available ? <TestBox config={config} /> : null}</div>
    </div>
  );
}

export function AiView() {
  const { data, error, mutate } = useSWR("chat-config", () => unwrap(browserApi.GET("/api/v1/chat/config")), {
    revalidateOnFocus: false,
  });
  return (
    <div className="mt-6">
      <h2 className="mb-4 text-lg font-bold">{t.title}</h2>
      {error ? <Notice kind="error">{errorText(error)}</Notice> : null}
      {!data ? (
        <p className="text-sm text-muted">{messages.common.loading}</p>
      ) : (
        <Form
          initial={data.config}
          available={data.ai_available}
          defaultModel={data.default_model}
          usage={data.usage}
          onSaved={() => void mutate()}
        />
      )}
    </div>
  );
}
