"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent, type ReactNode } from "react";
import { ArrowLeftIcon, PlusIcon, RefreshIcon, TrashIcon } from "@/components/icons";
import { Button, Card, Field, IconButton, Notice, Switch, inputClass, selectClass } from "@/components/ui";
import { ApiCallError } from "@/lib/api-error";
import { browserApi, unwrap } from "@/lib/api.client";
import { addKeywords } from "@/lib/campaign-form";
import {
  FOLLOWUP_MAX_MINUTES,
  MAX_OPTIONS,
  MESSAGE_MAX,
  OPTION_LABEL_MAX,
  emptyOption,
  emptyStep,
  nextStepId,
  removeStep,
  toFlowInput,
  validateDraft,
  type DraftErrors,
  type FlowDraft,
  type OptionDraft,
  type StepDraft,
} from "@/lib/chat";
import { messages } from "@/lib/messages";

const t = messages.chat.editor;

type Props = { mode: "create"; initial: FlowDraft } | { mode: "edit"; flowId: number; initial: FlowDraft };

function Err({ text }: { text?: string }) {
  return text ? <p className="mt-1 text-sm text-fail">{text}</p> : null;
}

function Labeled({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="font-semibold">{label}</span>
      <span className="mt-1 block">{children}</span>
    </label>
  );
}

function OptionEditor({
  option,
  steps,
  errors,
  at,
  onChange,
  onRemove,
}: {
  option: OptionDraft;
  steps: StepDraft[];
  errors: DraftErrors;
  at: string;
  onChange: (o: OptionDraft) => void;
  onRemove: () => void;
}) {
  const set = <K extends keyof OptionDraft>(key: K, value: OptionDraft[K]) =>
    onChange({ ...option, [key]: value });
  return (
    <div className="space-y-3 rounded-lg border border-line bg-surface p-3">
      <div className="flex items-end gap-2">
        <div className="flex-1">
          <Labeled label={t.optionLabel}>
            <input
              value={option.label}
              maxLength={OPTION_LABEL_MAX}
              onChange={(e) => set("label", e.target.value)}
              className={inputClass}
            />
          </Labeled>
        </div>
        <div>
          <Labeled label={t.action}>
            <select
              value={option.action}
              onChange={(e) => set("action", e.target.value as OptionDraft["action"])}
              className={selectClass}
            >
              {Object.entries(t.actions).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </Labeled>
        </div>
        <IconButton label={t.removeOption} onClick={onRemove}>
          <TrashIcon size={16} />
        </IconButton>
      </div>
      <Err text={errors[`${at}.label`]} />
      {option.action === "step" ? (
        <div>
          <Labeled label={t.targetStep}>
            <select value={option.step} onChange={(e) => set("step", e.target.value)} className={selectClass}>
              <option value="">{t.chooseStep}</option>
              {steps.map((s, n) => (
                <option key={s.id} value={s.id}>
                  {t.step(n + 1)} · {s.message.slice(0, 40) || s.id}
                </option>
              ))}
            </select>
          </Labeled>
          <Err text={errors[`${at}.step`]} />
        </div>
      ) : null}
      {option.action === "link" ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <Labeled label={t.optionUrl}>
              <input
                value={option.url}
                onChange={(e) => set("url", e.target.value)}
                placeholder="https://"
                className={inputClass}
              />
            </Labeled>
            <Err text={errors[`${at}.url`]} />
          </div>
          <Labeled label={t.optionLinkText}>
            <input value={option.linkText} onChange={(e) => set("linkText", e.target.value)} className={inputClass} />
          </Labeled>
          <Labeled label={t.optionLinkLabel}>
            <input
              value={option.linkLabel}
              maxLength={20}
              onChange={(e) => set("linkLabel", e.target.value)}
              className={inputClass}
            />
          </Labeled>
        </div>
      ) : null}
      <Labeled label={t.optionTags}>
        <input value={option.tags} onChange={(e) => set("tags", e.target.value)} className={inputClass} />
      </Labeled>
    </div>
  );
}

function StepEditor({
  step,
  index,
  draft,
  errors,
  onChange,
  onRemove,
  onMove,
}: {
  step: StepDraft;
  index: number;
  draft: FlowDraft;
  errors: DraftErrors;
  onChange: (s: StepDraft) => void;
  onRemove: () => void;
  onMove: (delta: number) => void;
}) {
  const at = `steps.${index}`;
  const set = <K extends keyof StepDraft>(key: K, value: StepDraft[K]) => onChange({ ...step, [key]: value });
  const after = step.options.length > 0 ? "options" : step.linkUrl || step.linkLabel ? "link" : "none";
  const [kind, setKind] = useState<"none" | "link" | "options">(after);

  function chooseKind(next: "none" | "link" | "options") {
    setKind(next);
    onChange({
      ...step,
      linkUrl: next === "link" ? step.linkUrl : "",
      linkLabel: next === "link" ? step.linkLabel : "",
      options: next === "options" ? (step.options.length ? step.options : [emptyOption()]) : [],
    });
  }

  return (
    <Card className="space-y-4 p-4">
      <div className="flex items-center justify-between gap-2">
        <p className="font-semibold">
          {t.step(index + 1)}
          {index === 0 ? <span className="ml-2 text-xs font-normal text-muted">{t.firstStep}</span> : null}
        </p>
        <div className="flex items-center gap-1">
          <IconButton label={t.moveUp} disabled={index === 0} onClick={() => onMove(-1)}>
            ↑
          </IconButton>
          <IconButton label={t.moveDown} disabled={index === draft.steps.length - 1} onClick={() => onMove(1)}>
            ↓
          </IconButton>
          <IconButton label={t.removeStep} disabled={draft.steps.length === 1} onClick={onRemove}>
            <TrashIcon size={16} />
          </IconButton>
        </div>
      </div>
      <Field label={t.message} hint={t.messageHint} error={errors[`${at}.message`]}>
        {(p) => (
          <textarea
            {...p}
            rows={3}
            maxLength={MESSAGE_MAX}
            value={step.message}
            onChange={(e) => set("message", e.target.value)}
            className={inputClass}
          />
        )}
      </Field>
      <fieldset>
        <legend className="text-sm font-semibold">{t.buttonsOrLink}</legend>
        <div className="mt-2 flex flex-wrap gap-2">
          {(["none", "link", "options"] as const).map((k) => (
            <button
              key={k}
              type="button"
              aria-pressed={kind === k}
              onClick={() => chooseKind(k)}
              className={`rounded-full px-3 py-1.5 text-sm font-medium ${
                kind === k ? "bg-accent text-accent-fg" : "bg-surface-2 text-muted hover:text-fg"
              }`}
            >
              {k === "none" ? t.kindNone : k === "link" ? t.kindLink : t.kindOptions}
            </button>
          ))}
        </div>
      </fieldset>
      {kind === "link" ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <Labeled label={t.linkUrl}>
              <input
                value={step.linkUrl}
                onChange={(e) => set("linkUrl", e.target.value)}
                placeholder="https://"
                className={inputClass}
              />
            </Labeled>
            <Err text={errors[`${at}.linkUrl`]} />
          </div>
          <Labeled label={t.linkLabel}>
            <input
              value={step.linkLabel}
              maxLength={20}
              onChange={(e) => set("linkLabel", e.target.value)}
              className={inputClass}
            />
          </Labeled>
        </div>
      ) : null}
      {kind === "options" ? (
        <div className="space-y-2">
          {step.options.map((o, j) => (
            <OptionEditor
              key={j}
              option={o}
              steps={draft.steps}
              errors={errors}
              at={`${at}.options.${j}`}
              onChange={(next) => set("options", step.options.map((x, k) => (k === j ? next : x)))}
              onRemove={() => set("options", step.options.filter((_, k) => k !== j))}
            />
          ))}
          <Button
            disabled={step.options.length >= MAX_OPTIONS}
            onClick={() => set("options", [...step.options, emptyOption()])}
          >
            <PlusIcon size={16} />
            {t.addOption}
          </Button>
        </div>
      ) : null}
      <Field label={t.tags} hint={t.tagsHint}>
        {(p) => <input {...p} value={step.tags} onChange={(e) => set("tags", e.target.value)} className={inputClass} />}
      </Field>
      <div className="rounded-xl bg-surface-2 p-4">
        <div className="flex items-start justify-between gap-4">
          <div className="text-sm">
            <p className="font-semibold">{t.followup}</p>
            <p className="mt-0.5 text-muted">{t.followupHint}</p>
          </div>
          <Switch checked={step.followupOn} onChange={(on) => set("followupOn", on)} label={t.followup} />
        </div>
        {step.followupOn ? (
          <div className="mt-4 space-y-3">
            <Labeled label={t.followupMinutes}>
              <input
                type="number"
                min={1}
                max={FOLLOWUP_MAX_MINUTES}
                value={step.followupMinutes}
                onChange={(e) => set("followupMinutes", Math.max(1, Math.min(FOLLOWUP_MAX_MINUTES, Number(e.target.value) || 1)))}
                className={`${inputClass} max-w-40`}
              />
            </Labeled>
            <Labeled label={t.followupMessage}>
              <textarea
                rows={2}
                maxLength={MESSAGE_MAX}
                value={step.followupMessage}
                onChange={(e) => set("followupMessage", e.target.value)}
                className={inputClass}
              />
            </Labeled>
            <Err text={errors[`${at}.followup`]} />
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={step.followupOnlyIfQuiet}
                onChange={(e) => set("followupOnlyIfQuiet", e.target.checked)}
                className="size-4"
              />
              {t.followupQuiet}
            </label>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

/** A tappable mock of the conversation: the first step, then wherever the taps lead. */
function Preview({ draft }: { draft: FlowDraft }) {
  const [path, setPath] = useState<{ stepId: string | null; text: string; tapped?: string }[]>([]);
  const first = draft.steps[0];
  const history = path.length ? path : first ? [{ stepId: first.id, text: first.message }] : [];
  const current = draft.steps.find((s) => s.id === history[history.length - 1]?.stepId);

  function tap(o: OptionDraft) {
    const next =
      o.action === "step"
        ? draft.steps.find((s) => s.id === o.step)
        : undefined;
    const reply =
      o.action === "step"
        ? { stepId: next?.id ?? null, text: next?.message ?? "…" }
        : o.action === "link"
          ? { stepId: null, text: `${o.linkText || "Aqui está:"}\n🔗 ${o.url}` }
          : { stepId: null, text: t.previewHandoff };
    setPath([...history, { ...reply, tapped: o.label }]);
  }

  return (
    <Card className="p-4">
      <div className="flex items-center justify-between">
        <div>
          <p className="font-semibold">{t.preview}</p>
          <p className="text-xs text-muted">{t.previewHint}</p>
        </div>
        <IconButton label={t.restart} onClick={() => setPath([])}>
          <RefreshIcon size={16} />
        </IconButton>
      </div>
      <div className="mt-4 space-y-2 rounded-2xl bg-neutral-950 p-4 text-sm text-white">
        {history.map((h, n) => (
          <div key={n} className="space-y-2">
            {h.tapped ? (
              <div className="flex justify-end">
                <span className="rounded-2xl bg-violet-600 px-3 py-1.5">{h.tapped}</span>
              </div>
            ) : null}
            <div className="flex justify-start">
              <span className="max-w-[85%] rounded-2xl bg-neutral-800 px-3 py-2 whitespace-pre-line">
                {(h.text || "…").replaceAll("{username}", "fulano")}
              </span>
            </div>
          </div>
        ))}
        {current?.linkUrl && current.options.length === 0 && history[history.length - 1]?.stepId === current.id ? (
          <div className="flex">
            <span className="rounded-lg border border-neutral-700 px-3 py-1.5 text-xs">
              {current.linkLabel || "Abrir"}
            </span>
          </div>
        ) : null}
        {current && history[history.length - 1]?.stepId === current.id && current.options.length > 0 ? (
          <div className="flex flex-wrap justify-end gap-1.5 pt-1">
            {current.options.map((o, j) => (
              <button
                key={j}
                type="button"
                onClick={() => tap(o)}
                className="rounded-full border border-violet-400 px-3 py-1 text-xs text-violet-200 hover:bg-violet-500/20"
              >
                {o.label || "…"}
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </Card>
  );
}

export function FlowEditor(props: Props) {
  const router = useRouter();
  const [draft, setDraft] = useState<FlowDraft>(props.initial);
  const [keywordText, setKeywordText] = useState("");
  const [errors, setErrors] = useState<DraftErrors>({});
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const set = <K extends keyof FlowDraft>(key: K, value: FlowDraft[K]) => setDraft({ ...draft, [key]: value });
  const setStep = (i: number, s: StepDraft) => set("steps", draft.steps.map((x, k) => (k === i ? s : x)));

  function moveStep(i: number, delta: number) {
    const steps = [...draft.steps];
    const [moved] = steps.splice(i, 1);
    if (!moved) return;
    steps.splice(i + delta, 0, moved);
    set("steps", steps);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const withTyped = keywordText.trim()
      ? { ...draft, keywords: addKeywords(draft.keywords, keywordText, 20) }
      : draft;
    const found = validateDraft(withTyped);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      setFlash({ kind: "error", text: t.fixErrors });
      return;
    }
    setBusy(true);
    setFlash(null);
    try {
      const body = toFlowInput(withTyped);
      const saved =
        props.mode === "create"
          ? await unwrap(browserApi.POST("/api/v1/chat/flows", { body }))
          : await unwrap(
              browserApi.PUT("/api/v1/chat/flows/{flow_id}", {
                params: { path: { flow_id: props.flowId } },
                body,
              }),
            );
      setFlash({ kind: "ok", text: t.saved });
      if (props.mode === "create") router.push(`/chat/flows/${saved.id}`);
      setDraft(withTyped);
      setKeywordText("");
    } catch (e) {
      setFlash({ kind: "error", text: e instanceof ApiCallError ? e.message : messages.common.loadFailed });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="flex items-center gap-3">
        <Link
          href="/chat/flows"
          aria-label={t.back}
          title={t.back}
          className="grid size-9 place-items-center rounded-lg text-muted transition-colors hover:bg-surface-2 hover:text-fg"
        >
          <ArrowLeftIcon size={20} />
        </Link>
        <h1 className="text-2xl font-bold tracking-tight">
          {props.mode === "create" ? t.titleNew : t.titleEdit}
        </h1>
      </div>
      <form
        onSubmit={submit}
        noValidate
        className="mt-6 grid items-start gap-6 lg:grid-cols-[minmax(0,40rem)_minmax(0,1fr)]"
      >
        <div className="space-y-4">
          <Card className="space-y-4 p-4">
            <Field label={t.name} hint={t.nameHint} error={errors.name}>
              {(p) => (
                <input
                  {...p}
                  value={draft.name}
                  maxLength={80}
                  onChange={(e) => set("name", e.target.value)}
                  className={inputClass}
                />
              )}
            </Field>
            <div className="flex items-center justify-between gap-4">
              <p className="text-sm font-semibold">{t.active}</p>
              <Switch checked={draft.isActive} onChange={(on) => set("isActive", on)} label={t.active} />
            </div>
            <Field label={t.keywords} hint={t.keywordsHint}>
              {(p) => (
                <div className="space-y-2">
                  <div className="flex gap-2">
                    <input
                      {...p}
                      value={keywordText}
                      onChange={(e) => setKeywordText(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === ",") {
                          e.preventDefault();
                          set("keywords", addKeywords(draft.keywords, keywordText, 20));
                          setKeywordText("");
                        }
                      }}
                      placeholder="menu, ajuda"
                      className={inputClass}
                    />
                    <select
                      value={draft.matchMode}
                      onChange={(e) => set("matchMode", e.target.value as FlowDraft["matchMode"])}
                      className={selectClass}
                      aria-label={t.keywords}
                    >
                      {Object.entries(t.matchMode).map(([k, label]) => (
                        <option key={k} value={k}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </div>
                  {draft.keywords.length > 0 ? (
                    <ul className="flex flex-wrap gap-1.5">
                      {draft.keywords.map((k) => (
                        <li key={k}>
                          <button
                            type="button"
                            onClick={() => set("keywords", draft.keywords.filter((x) => x !== k))}
                            className="rounded-full bg-accent-soft px-2.5 py-1 text-xs font-semibold text-accent"
                          >
                            {k} ×
                          </button>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              )}
            </Field>
          </Card>
          {draft.steps.map((s, i) => (
            <StepEditor
              key={`${s.id}-${i}`}
              step={s}
              index={i}
              draft={draft}
              errors={errors}
              onChange={(next) => setStep(i, next)}
              onRemove={() => setDraft(removeStep(draft, i))}
              onMove={(d) => moveStep(i, d)}
            />
          ))}
          <Button onClick={() => set("steps", [...draft.steps, emptyStep(nextStepId(draft.steps))])}>
            <PlusIcon size={16} />
            {t.addStep}
          </Button>
          {flash ? <Notice kind={flash.kind}>{flash.text}</Notice> : null}
          <Button type="submit" variant="primary" disabled={busy}>
            {busy ? t.saving : t.save}
          </Button>
        </div>
        <div className="lg:sticky lg:top-6">
          <Preview draft={draft} />
        </div>
      </form>
    </>
  );
}
