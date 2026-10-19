"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import useSWR from "swr";
import {
  BoltIcon,
  CommentIcon,
  CopyIcon,
  LockIcon,
  PencilIcon,
  PlusIcon,
  SearchIcon,
  SendIcon,
  StoryIcon,
  TrashIcon,
} from "@/components/icons";
import { PostThumb } from "@/components/post-thumb";
import {
  Button,
  Card,
  ConfirmDialog,
  IconButton,
  IconLink,
  LinkButton,
  Notice,
  PageHeader,
  Pill,
  Switch,
  inputClass,
  selectClass,
} from "@/components/ui";
import { ApiCallError } from "@/lib/api-error";
import { browserApi, unwrap } from "@/lib/api.client";
import {
  NO_FILTERS,
  filterCampaigns,
  isFiltered,
  sortByRecent,
  triggerLines,
  type Campaign,
  type ListFilters,
  type StateFilter,
  type TriggerFilter,
} from "@/lib/campaign-list";
import { relativeTime } from "@/lib/format";
import { messages } from "@/lib/messages";
import { indexPosts, postLabel } from "@/lib/posts";
import { usePosts } from "@/lib/use-posts";

const t = messages.campaigns;
const KEY = "campaigns";
// Name and trigger on the left, then the numbers, then the actions.
const COLUMNS =
  "md:grid md:grid-cols-[minmax(0,1fr)_4.5rem_4.5rem_6.5rem_auto] md:items-center md:gap-4";

type Flash = { kind: "ok" | "error"; text: string } | null;

function Metric({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-1.5 md:block md:text-right">
      <span className="text-xs text-muted md:hidden">{label}</span>
      <span className="font-semibold tabular-nums">{children}</span>
    </div>
  );
}

export function CampaignsList({ initialNotice }: { initialNotice: string | null }) {
  const { data, error, isLoading, mutate } = useSWR(
    KEY,
    () => unwrap(browserApi.GET("/api/v1/campaigns")),
    { refreshInterval: 10_000 },
  );
  // Thumbnails only matter when some campaign points at one post, so only then ask Meta.
  const needsPosts = data?.some((c) => c.trigger_comments && c.post_id) ?? false;
  const posts = usePosts(needsPosts);
  const postsById = useMemo(() => indexPosts(posts.data), [posts.data]);

  const [filters, setFilters] = useState<ListFilters>(NO_FILTERS);
  const [flash, setFlash] = useState<Flash>(
    initialNotice ? { kind: "ok", text: initialNotice } : null,
  );
  const [busyId, setBusyId] = useState<number | null>(null);
  const [toDelete, setToDelete] = useState<Campaign | null>(null);

  async function run(id: number, action: () => Promise<unknown>, success: string) {
    setBusyId(id);
    setFlash(null);
    try {
      await action();
      setFlash({ kind: "ok", text: success });
      await mutate();
    } catch (e) {
      setFlash({
        kind: "error",
        text: e instanceof ApiCallError ? e.message : messages.common.loadFailed,
      });
    } finally {
      setBusyId(null);
    }
  }

  const params = (id: number) => ({ params: { path: { campaign_id: id } } });
  const toggle = (c: Campaign, next: boolean) =>
    run(
      c.id,
      () =>
        next
          ? unwrap(browserApi.POST("/api/v1/campaigns/{campaign_id}/resume", params(c.id)))
          : unwrap(browserApi.POST("/api/v1/campaigns/{campaign_id}/pause", params(c.id))),
      next ? t.resumed_done : t.paused_done,
    );
  const duplicate = (c: Campaign) =>
    run(
      c.id,
      () => unwrap(browserApi.POST("/api/v1/campaigns/{campaign_id}/duplicate", params(c.id))),
      t.duplicated,
    );
  async function confirmDelete() {
    const campaign = toDelete;
    setToDelete(null);
    if (!campaign) return;
    await run(
      campaign.id,
      () => unwrap(browserApi.DELETE("/api/v1/campaigns/{campaign_id}", params(campaign.id))),
      t.deleted,
    );
  }

  const visible = data ? sortByRecent(filterCampaigns(data, filters)) : [];
  const set = <K extends keyof ListFilters>(key: K, value: ListFilters[K]) =>
    setFilters((current) => ({ ...current, [key]: value }));

  return (
    <>
      <PageHeader
        title={t.title}
        actions={
          <LinkButton href="/campaigns/new" variant="primary">
            <PlusIcon size={18} />
            {t.new}
          </LinkButton>
        }
      />

      <div className="mt-6 flex flex-wrap gap-3">
        <div className="relative min-w-56 flex-1 sm:max-w-xs">
          <SearchIcon
            size={18}
            className="pointer-events-none absolute top-1/2 left-3.5 -translate-y-1/2 text-muted"
          />
          <input
            type="search"
            aria-label={t.search}
            placeholder={t.search}
            value={filters.query}
            onChange={(e) => set("query", e.target.value)}
            className={`${inputClass} pl-10`}
          />
        </div>
        <select
          aria-label={t.filters.trigger}
          value={filters.trigger}
          onChange={(e) => set("trigger", e.target.value as TriggerFilter)}
          className={`${selectClass} min-w-44`}
        >
          <option value="all">{t.filters.anyTrigger}</option>
          <option value="comments">{t.filters.comments}</option>
          <option value="stories">{t.filters.stories}</option>
          <option value="messages">{t.filters.messages}</option>
          <option value="gate">{t.filters.gate}</option>
        </select>
        <select
          aria-label={t.filters.state}
          value={filters.state}
          onChange={(e) => set("state", e.target.value as StateFilter)}
          className={`${selectClass} min-w-44`}
        >
          <option value="all">{t.filters.anyState}</option>
          <option value="active">{t.filters.live}</option>
          <option value="paused">{t.filters.paused}</option>
        </select>
      </div>

      <div className="mt-4 min-h-12" aria-live="polite">
        {flash ? <Notice kind={flash.kind}>{flash.text}</Notice> : null}
      </div>

      {isLoading ? (
        <p className="text-muted">{messages.common.loading}</p>
      ) : error || !data ? (
        <p role="alert" className="text-fail">
          {messages.common.loadFailed}
        </p>
      ) : data.length === 0 ? (
        <Card className="mt-2 px-6 py-14 text-center">
          <span className="mx-auto grid size-12 place-items-center rounded-full bg-accent-soft text-accent">
            <BoltIcon size={24} />
          </span>
          <h2 className="mt-4 text-lg font-bold">{t.emptyTitle}</h2>
          <p className="mx-auto mt-1 max-w-sm text-muted">{t.emptyBody}</p>
          <div className="mt-5 flex justify-center">
            <LinkButton href="/campaigns/new" variant="primary">
              <PlusIcon size={18} />
              {t.new}
            </LinkButton>
          </div>
        </Card>
      ) : visible.length === 0 ? (
        <Card className="mt-2 px-6 py-10 text-center">
          <p className="text-muted">{t.noResults}</p>
          {isFiltered(filters) ? (
            <div className="mt-4 flex justify-center">
              <Button onClick={() => setFilters(NO_FILTERS)}>{t.clearFilters}</Button>
            </div>
          ) : null}
        </Card>
      ) : (
        <>
          <div
            aria-hidden
            className={`${COLUMNS} hidden px-4 pb-2 text-xs font-semibold text-muted`}
          >
            <span>{t.columns.name}</span>
            <span className="text-right">{t.columns.sent}</span>
            <span className="text-right">{t.columns.failed}</span>
            <span>{t.modified}</span>
            <span className="w-[10.5rem]" />
          </div>
          <ul className="space-y-3">
            {visible.map((c) => (
              <li key={c.id}>
                <Card className={`p-4 ${COLUMNS}`}>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2.5">
                      <Pill tone={c.is_active ? "ok" : "muted"} dot>
                        {c.is_active ? t.active : t.paused}
                      </Pill>
                      <Link
                        href={`/campaigns/${c.id}/edit`}
                        className="min-w-0 truncate text-base font-bold hover:underline"
                      >
                        {c.name}
                      </Link>
                    </div>
                    <ul className="mt-2.5 space-y-1.5 text-sm text-muted">
                      {triggerLines(c).map((line) => {
                        if (line.kind === "stories") {
                          return (
                            <li key="stories" className="flex items-center gap-2">
                              <StoryIcon size={16} />
                              {t.storyRow}
                            </li>
                          );
                        }
                        if (line.kind === "messages") {
                          return (
                            <li key="messages" className="flex items-center gap-2">
                              <SendIcon size={16} />
                              {t.messagesRow}
                            </li>
                          );
                        }
                        if (line.kind === "gate") {
                          return (
                            <li key="gate" className="flex items-center gap-2">
                              <LockIcon size={16} />
                              {t.gateRow}
                            </li>
                          );
                        }
                        const post = line.postId ? postsById.get(line.postId) : undefined;
                        return (
                          <li key="comments" className="flex min-w-0 items-center gap-2">
                            <CommentIcon size={16} className="shrink-0" />
                            {line.postId === null ? (
                              t.commentOnAnyPost
                            ) : (
                              <>
                                <span className="shrink-0">{t.commentOnPost}</span>
                                {post ? (
                                  <>
                                    <PostThumb post={post} className="size-6 rounded-md" />
                                    <span className="min-w-0 truncate">{postLabel(post)}</span>
                                  </>
                                ) : (
                                  <span className="truncate">{t.post(line.postId)}</span>
                                )}
                              </>
                            )}
                          </li>
                        );
                      })}
                    </ul>
                    <ul className="mt-3 flex flex-wrap gap-1.5">
                      {c.keywords.slice(0, 4).map((keyword) => (
                        <li
                          key={keyword}
                          className="rounded-md bg-surface-2 px-2 py-0.5 text-xs font-medium"
                        >
                          {keyword}
                        </li>
                      ))}
                      {c.keywords.length > 4 ? (
                        <li className="px-1 py-0.5 text-xs text-muted">
                          {t.moreKeywords(c.keywords.length - 4)}
                        </li>
                      ) : null}
                    </ul>
                  </div>

                  <div className="mt-4 flex flex-wrap gap-x-6 gap-y-1 md:contents">
                    <Metric label={t.columns.sent}>{c.deliveries.sent}</Metric>
                    <div className="md:text-right">
                      <Metric label={t.columns.failed}>{c.deliveries.failed}</Metric>
                      {c.deliveries.unconfirmed > 0 ? (
                        <Link
                          href={`/logs?campaign=${c.id}&status=UNCONFIRMED`}
                          className="block text-xs font-medium text-unconf hover:underline"
                        >
                          {t.unconfirmedHint(c.deliveries.unconfirmed)}
                        </Link>
                      ) : null}
                    </div>
                    <span className="text-sm text-muted">{relativeTime(c.updated_at)}</span>
                  </div>

                  <div className="mt-3 flex items-center justify-end gap-1 md:mt-0">
                    <Switch
                      checked={c.is_active}
                      label={t.toggleLabel(c.name)}
                      disabled={busyId === c.id}
                      onChange={(next) => toggle(c, next)}
                    />
                    <span className="mx-1 h-5 w-px bg-line" aria-hidden />
                    <IconLink
                      href={`/campaigns/${c.id}/edit`}
                      label={t.rowAction(messages.common.edit, c.name)}
                    >
                      <PencilIcon size={18} />
                    </IconLink>
                    <IconButton
                      label={t.rowAction(messages.common.duplicate, c.name)}
                      disabled={busyId === c.id}
                      onClick={() => duplicate(c)}
                    >
                      <CopyIcon size={18} />
                    </IconButton>
                    <IconButton
                      label={t.rowAction(messages.common.delete, c.name)}
                      disabled={busyId === c.id}
                      onClick={() => setToDelete(c)}
                      className="hover:text-fail"
                    >
                      <TrashIcon size={18} />
                    </IconButton>
                  </div>
                </Card>
              </li>
            ))}
          </ul>
        </>
      )}

      <ConfirmDialog
        open={toDelete !== null}
        title={t.confirmDeleteTitle(toDelete?.name ?? "")}
        confirmLabel={t.deleteAction}
        onConfirm={confirmDelete}
        onCancel={() => setToDelete(null)}
      >
        <p>{t.confirmDeleteBody}</p>
      </ConfirmDialog>
    </>
  );
}
