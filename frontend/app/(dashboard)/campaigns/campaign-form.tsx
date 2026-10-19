"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import useSWR from "swr";
import { ArrowLeftIcon } from "@/components/icons";
import { PhonePreview } from "@/components/phone-preview";
import { PostPicker } from "@/components/post-picker";
import { Button, Card, Field, Notice, OptionCard, ToggleCard, inputClass } from "@/components/ui";
import { ApiCallError } from "@/lib/api-error";
import { browserApi, unwrap } from "@/lib/api.client";
import {
  addKeywords,
  toPayload,
  validateForm,
  type CampaignForm as FormState,
  type FormErrors,
} from "@/lib/campaign-form";
import { messages } from "@/lib/messages";
import {
  availableTabs,
  dmThread,
  pickTab,
  sampleComment,
  type PreviewTab,
} from "@/lib/phone-preview";
import { indexPosts } from "@/lib/posts";
import { usePosts } from "@/lib/use-posts";

const t = messages.builder;
const phone = t.phone;

// Which error key each form field reports under (the API's field names).
const ERROR_KEY: Record<keyof FormState, string> = {
  triggerComments: "trigger",
  triggerStoryReplies: "trigger",
  triggerMessages: "trigger",
  name: "name",
  target: "post_id",
  postId: "post_id",
  keywords: "keywords",
  matchMode: "keywords",
  message: "dm_message",
  linkUrl: "link_url",
  buttonLabel: "link_button_label",
  followGateEnabled: "follow_gate_enabled",
  followGateMessage: "follow_gate_message",
  followGateButtonLabel: "follow_gate_button_label",
  publicReplies: "public_reply_messages",
};

const FALLBACK_LIMITS = {
  name_max: 80,
  keywords_max: 20,
  keyword_max: 100,
  message_max: 1000,
  button_title_max: 20,
  link_max: 2048,
  public_replies_max: 10,
  public_reply_max: 300,
  gate_message_max: 640,
};

type Props =
  | { mode: "create"; initial: FormState }
  | { mode: "edit"; campaignId: number; initial: FormState };

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="text-base font-bold">{title}</h2>
      {children}
    </section>
  );
}

/** A grey card with no choice in it, only fields. */
function Panel({ children }: { children: ReactNode }) {
  return <div className="space-y-5 rounded-xl bg-surface-2 p-4">{children}</div>;
}

export function CampaignForm(props: Props) {
  const router = useRouter();
  const [form, setForm] = useState<FormState>(props.initial);
  const [errors, setErrors] = useState<FormErrors>({});
  const [flash, setFlash] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [createPaused, setCreatePaused] = useState(false);
  const [keywordDraft, setKeywordDraft] = useState("");
  const [testText, setTestText] = useState("");
  const [testResult, setTestResult] = useState<{ matched: boolean; keyword: string | null } | null>(
    null,
  );
  const [wantedTab, setWantedTab] = useState<PreviewTab>("post");

  const { data: limitsData } = useSWR("limits", () => unwrap(browserApi.GET("/api/v1/limits")), {
    revalidateOnFocus: false,
  });
  const limits = limitsData ?? FALLBACK_LIMITS;

  // Posts feed both the picker and the phone, so they are asked for once, here.
  const posts = usePosts(form.triggerComments);
  const postsById = useMemo(() => indexPosts(posts.data), [posts.data]);
  const accountQuery = useSWR("account", () => unwrap(browserApi.GET("/api/v1/account")), {
    revalidateOnFocus: false,
  });
  const account = accountQuery.data?.username ?? phone.yourAccount;

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((current) => ({ ...current, [key]: value }));
    setErrors((current) => {
      if (!current[ERROR_KEY[key]] && !current._form) return current;
      const { [ERROR_KEY[key]]: _drop, _form: _dropForm, ...rest } = current;
      void _drop;
      void _dropForm;
      return rest;
    });
  }

  const testable = testText.trim() !== "" && form.keywords.length > 0;
  useEffect(() => {
    if (!testable) return;
    let stale = false;
    const timer = setTimeout(async () => {
      try {
        const result = await unwrap(
          browserApi.POST("/api/v1/campaigns/match-test", {
            body: { keywords: form.keywords, match_mode: form.matchMode, text: testText },
          }),
        );
        if (!stale) setTestResult(result);
      } catch {
        if (!stale) setTestResult(null);
      }
    }, 300);
    return () => {
      stale = true;
      clearTimeout(timer);
    };
  }, [testable, testText, form.keywords, form.matchMode]);
  const shownResult = testable ? testResult : null;

  function commitKeywordDraft() {
    if (!keywordDraft.trim()) return;
    set("keywords", addKeywords(form.keywords, keywordDraft, limits.keywords_max));
    setKeywordDraft("");
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFlash(null);
    const pending = keywordDraft.trim()
      ? addKeywords(form.keywords, keywordDraft, limits.keywords_max)
      : form.keywords;
    const current = { ...form, keywords: pending };
    const problems = validateForm(current, limits);
    if (Object.keys(problems).length > 0) {
      setErrors(problems);
      setFlash({ kind: "error", text: t.fixErrors });
      return;
    }
    setSubmitting(true);
    try {
      const payload = toPayload(current);
      if (props.mode === "create") {
        await unwrap(
          browserApi.POST("/api/v1/campaigns", { body: { ...payload, is_active: !createPaused } }),
        );
      } else {
        await unwrap(
          browserApi.PATCH("/api/v1/campaigns/{campaign_id}", {
            params: { path: { campaign_id: props.campaignId } },
            body: payload,
          }),
        );
      }
      router.push(`/campaigns?done=${props.mode === "create" ? "created" : "updated"}`);
    } catch (e) {
      const info = e instanceof ApiCallError ? e.info : null;
      const fieldErrors = info ? Object.keys(info.fields).filter((k) => k !== "_form") : [];
      setErrors((info?.fields ?? {}) as FormErrors);
      setFlash({
        kind: "error",
        text: fieldErrors.length > 0 ? t.fixErrors : (info?.message ?? messages.common.loadFailed),
      });
      setSubmitting(false);
    }
  }

  // What the phone shows. It reads the form as it is typed, so nothing needs sending to check.
  const tabs = availableTabs(form);
  const tab = pickTab(wantedTab, tabs);
  const selectedPost =
    form.triggerComments && form.target === "post" ? postsById.get(form.postId) : undefined;
  const publicReply = form.triggerComments
    ? (form.publicReplies.map((m) => m.trim()).find(Boolean) ?? null)
    : null;
  const thread = dmThread({
    message: form.message,
    linkUrl: form.linkUrl,
    buttonLabel: form.buttonLabel,
    gate: {
      enabled: form.followGateEnabled,
      message: form.followGateMessage,
      buttonLabel: form.followGateButtonLabel,
    },
  });
  const linkExtra = form.linkUrl.trim() ? form.linkUrl.trim().length + 1 : 0;
  const messageSize = form.message.length + linkExtra;
  const isCreate = props.mode === "create";
  const containsTitle =
    form.triggerComments && !form.triggerStoryReplies && !form.triggerMessages
      ? t.sectionContainsComment
      : form.triggerStoryReplies && !form.triggerComments && !form.triggerMessages
        ? t.sectionContainsStory
        : t.sectionContainsBoth;

  return (
    <>
      <div className="flex items-center gap-3">
        <Link
          href="/campaigns"
          aria-label={t.back}
          title={t.back}
          className="grid size-9 place-items-center rounded-lg text-muted transition-colors hover:bg-surface-2 hover:text-fg"
        >
          <ArrowLeftIcon size={20} />
        </Link>
        <h1 className="text-2xl font-bold tracking-tight">
          {isCreate ? t.titleNew : t.titleEdit}
        </h1>
      </div>

      <form
        onSubmit={onSubmit}
        noValidate
        className="mt-6 grid items-start gap-8 lg:grid-cols-[minmax(0,34rem)_minmax(0,1fr)]"
      >
        <Card className="p-5">
          <div className="space-y-8">
            <Field label={t.name} hint={t.nameHint} error={errors.name}>
              {(p) => (
                <input
                  {...p}
                  value={form.name}
                  maxLength={limits.name_max}
                  onChange={(e) => set("name", e.target.value)}
                  className={inputClass}
                />
              )}
            </Field>

            <Section title={t.sectionWhen}>
              <OptionCard
                type="checkbox"
                checked={form.triggerComments}
                onChange={(on) => set("triggerComments", on)}
                title={t.commentsCard}
                description={t.commentsCardHint}
              >
                <div className="space-y-3">
                  <OptionCard
                    nested
                    type="radio"
                    name="target"
                    checked={form.target === "post"}
                    onChange={() => set("target", "post")}
                    title={t.specificPost}
                  >
                    <PostPicker
                      query={posts}
                      selected={form.postId}
                      onSelect={(id) => set("postId", id)}
                    />
                    {errors.post_id ? (
                      <p className="mt-2 text-sm text-fail">{errors.post_id}</p>
                    ) : null}
                  </OptionCard>
                  <OptionCard
                    nested
                    type="radio"
                    name="target"
                    checked={form.target === "any"}
                    onChange={() => set("target", "any")}
                    title={t.anyPostCard}
                    description={t.targetAnyHint}
                  />
                </div>
              </OptionCard>
              <OptionCard
                type="checkbox"
                checked={form.triggerStoryReplies}
                onChange={(on) => set("triggerStoryReplies", on)}
                title={t.storyCard}
                description={t.triggerStoryHint}
              />
              <OptionCard
                type="checkbox"
                checked={form.triggerMessages}
                onChange={(on) => set("triggerMessages", on)}
                title={t.messagesCard}
                description={t.messagesCardHint}
              />
              {errors.trigger ? <p className="text-sm text-fail">{errors.trigger}</p> : null}
            </Section>

            <Section title={containsTitle}>
              <Panel>
                <Field label={t.keywords} hint={t.keywordsHint} error={errors.keywords}>
                  {(p) => (
                    <div className="flex gap-2">
                      <input
                        {...p}
                        value={keywordDraft}
                        maxLength={limits.keyword_max * 4}
                        onChange={(e) => setKeywordDraft(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            commitKeywordDraft();
                          }
                        }}
                        onBlur={commitKeywordDraft}
                        className={inputClass}
                      />
                      <Button onClick={commitKeywordDraft}>{t.keywordAdd}</Button>
                    </div>
                  )}
                </Field>

                {form.keywords.length > 0 ? (
                  <ul className="flex flex-wrap gap-2">
                    {form.keywords.map((keyword) => (
                      <li
                        key={keyword}
                        className="inline-flex items-center gap-1 rounded-full bg-accent-soft py-1 pr-1 pl-3 text-sm font-semibold text-accent"
                      >
                        {keyword}
                        <button
                          type="button"
                          aria-label={t.keywordRemove(keyword)}
                          onClick={() =>
                            set(
                              "keywords",
                              form.keywords.filter((k) => k !== keyword),
                            )
                          }
                          className="grid size-6 place-items-center rounded-full hover:bg-accent/15"
                        >
                          <span aria-hidden>×</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div className="flex flex-wrap items-center gap-2 text-sm text-muted">
                    <span>{t.examples}:</span>
                    {t.exampleWords.map((word) => (
                      <button
                        key={word}
                        type="button"
                        aria-label={t.addExample(word)}
                        onClick={() =>
                          set("keywords", addKeywords(form.keywords, word, limits.keywords_max))
                        }
                        className="rounded-md border border-line bg-surface px-2 py-0.5 text-xs font-medium text-fg hover:bg-surface-2"
                      >
                        {word}
                      </button>
                    ))}
                  </div>
                )}

                <div role="radiogroup" aria-label={t.matchMode} className="space-y-2.5">
                  <label className="flex items-start gap-3 text-sm">
                    <input
                      type="radio"
                      name="match"
                      className="mt-0.5 size-4"
                      checked={form.matchMode === "WHOLE_WORD"}
                      onChange={() => set("matchMode", "WHOLE_WORD")}
                    />
                    <span>
                      <span className="font-semibold">{t.matchWhole}</span>
                      <span className="block text-muted">{t.matchWholeHint}</span>
                    </span>
                  </label>
                  <label className="flex items-start gap-3 text-sm">
                    <input
                      type="radio"
                      name="match"
                      className="mt-0.5 size-4"
                      checked={form.matchMode === "CONTAINS"}
                      onChange={() => set("matchMode", "CONTAINS")}
                    />
                    <span>
                      <span className="font-semibold">{t.matchContains}</span>
                      <span className="block text-muted">{t.matchContainsHint}</span>
                    </span>
                  </label>
                </div>

                <div>
                  <Field label={t.tester} hint={t.testerHint}>
                    {(p) => (
                      <input
                        {...p}
                        value={testText}
                        onChange={(e) => setTestText(e.target.value)}
                        className={inputClass}
                      />
                    )}
                  </Field>
                  <p aria-live="polite" className="mt-2 min-h-5 text-sm">
                    {shownResult ? (
                      shownResult.matched ? (
                        <span className="font-medium text-ok">
                          {t.testMatch(shownResult.keyword ?? "")}
                        </span>
                      ) : (
                        <span className="text-muted">{t.testNoMatch}</span>
                      )
                    ) : null}
                  </p>
                </div>
              </Panel>
            </Section>

            <Section title={t.sectionThen}>
              <Panel>
                <p className="text-sm font-semibold">{t.dmCard}</p>
                <Field
                  label={t.message}
                  hint={`${t.messageHint} ${messageSize}/${limits.message_max}`}
                  error={errors.dm_message}
                >
                  {(p) => (
                    <textarea
                      {...p}
                      rows={4}
                      value={form.message}
                      onChange={(e) => set("message", e.target.value)}
                      className={inputClass}
                    />
                  )}
                </Field>
                <Field label={t.link} hint={t.linkHint} error={errors.link_url}>
                  {(p) => (
                    <input
                      {...p}
                      type="url"
                      inputMode="url"
                      value={form.linkUrl}
                      maxLength={limits.link_max}
                      onChange={(e) => set("linkUrl", e.target.value)}
                      className={inputClass}
                    />
                  )}
                </Field>
                <Field label={t.button} hint={t.buttonHint} error={errors.link_button_label}>
                  {(p) => (
                    <input
                      {...p}
                      value={form.buttonLabel}
                      maxLength={limits.button_title_max + 10}
                      onChange={(e) => set("buttonLabel", e.target.value)}
                      className={inputClass}
                    />
                  )}
                </Field>
              </Panel>

              <ToggleCard
                title={t.gateSwitch}
                description={t.gateSwitchHint}
                checked={form.followGateEnabled}
                onChange={(on) => set("followGateEnabled", on)}
              >
                <Field
                  label={t.gateMessage}
                  hint={`${t.gateMessageHint} ${form.followGateMessage.length}/${limits.gate_message_max}`}
                  error={errors.follow_gate_message}
                >
                  {(p) => (
                    <textarea
                      {...p}
                      rows={3}
                      value={form.followGateMessage}
                      onChange={(e) => set("followGateMessage", e.target.value)}
                      className={inputClass}
                    />
                  )}
                </Field>
                <Field
                  label={t.gateButton}
                  hint={t.gateButtonHint}
                  error={errors.follow_gate_button_label}
                >
                  {(p) => (
                    <input
                      {...p}
                      value={form.followGateButtonLabel}
                      maxLength={limits.button_title_max + 10}
                      onChange={(e) => set("followGateButtonLabel", e.target.value)}
                      className={inputClass}
                    />
                  )}
                </Field>
              </ToggleCard>

              {form.triggerComments ? (
                <ToggleCard
                  title={t.publicSwitch}
                  description={t.publicSwitchHint}
                  checked={form.publicReplies.length > 0}
                  onChange={(on) => set("publicReplies", on ? [""] : [])}
                >
                  {form.publicReplies.map((reply, index) => (
                    <div key={index} className="flex items-start gap-2">
                      <div className="min-w-0 flex-1">
                        <Field
                          label={t.publicItem(index + 1)}
                          error={index === 0 ? errors.public_reply_messages : undefined}
                        >
                          {(p) => (
                            <textarea
                              {...p}
                              rows={2}
                              value={reply}
                              onChange={(e) =>
                                set(
                                  "publicReplies",
                                  form.publicReplies.map((m, i) =>
                                    i === index ? e.target.value : m,
                                  ),
                                )
                              }
                              className={inputClass}
                            />
                          )}
                        </Field>
                      </div>
                      <div className="pt-7">
                        <Button
                          variant="ghost"
                          onClick={() =>
                            set(
                              "publicReplies",
                              form.publicReplies.filter((_, i) => i !== index),
                            )
                          }
                        >
                          {t.publicRemove}
                        </Button>
                      </div>
                    </div>
                  ))}
                  <Button
                    disabled={form.publicReplies.length >= limits.public_replies_max}
                    onClick={() => set("publicReplies", [...form.publicReplies, ""])}
                  >
                    {t.publicAdd}
                  </Button>
                </ToggleCard>
              ) : null}
            </Section>
          </div>

          <div className="sticky bottom-0 z-10 -mx-5 mt-8 -mb-5 space-y-3 rounded-b-xl border-t border-line bg-surface p-5">
            {isCreate ? (
              <label className="flex items-start gap-3 text-sm">
                <input
                  type="checkbox"
                  className="mt-0.5 size-4"
                  checked={createPaused}
                  onChange={(e) => setCreatePaused(e.target.checked)}
                />
                <span>
                  <span className="font-semibold">{t.createPaused}</span>
                  <span className="block text-muted">{t.createPausedHint}</span>
                </span>
              </label>
            ) : null}
            <div aria-live="polite">
              {flash ? <Notice kind={flash.kind}>{flash.text}</Notice> : null}
            </div>
            <Button type="submit" variant="primary" disabled={submitting} className="w-full">
              {submitting
                ? isCreate
                  ? t.creating
                  : messages.common.saving
                : isCreate
                  ? t.create
                  : messages.common.save}
            </Button>
          </div>
        </Card>

        <aside className="lg:sticky lg:top-8">
          <PhonePreview
            tab={tab}
            tabs={tabs}
            onTab={setWantedTab}
            account={account}
            post={selectedPost}
            postHint={form.target === "any" ? phone.anyPostHint : phone.pickPostHint}
            comment={sampleComment(form.keywords)}
            publicReply={publicReply}
            thread={thread}
          />
        </aside>
      </form>
    </>
  );
}
