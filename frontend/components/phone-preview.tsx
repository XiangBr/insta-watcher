"use client";

import { CommentIcon, HeartIcon, SendIcon } from "@/components/icons";
import { PostThumb } from "@/components/post-thumb";
import { renderDm } from "@/lib/dm-preview";
import { messages } from "@/lib/messages";
import type { PreviewTab, ThreadMessage } from "@/lib/phone-preview";
import { captionLine, type Post } from "@/lib/posts";

const t = messages.builder;
const p = t.phone;

// The phone stays dark in both themes, like the screen it imitates, so these colours are fixed
// on purpose and do not follow the dashboard's tokens.

function Avatar({ name }: { name: string }) {
  return (
    <span className="grid size-7 shrink-0 place-items-center rounded-full bg-neutral-700 text-xs font-semibold text-white uppercase ring-2 ring-rose-400/80">
      {name.charAt(0)}
    </span>
  );
}

function PostScreen({
  account,
  post,
  hint,
}: {
  account: string;
  post: Post | undefined;
  hint: string | null;
}) {
  const caption = post ? captionLine(post, 140) : "";
  return (
    <div>
      <div className="flex items-center gap-2.5 px-3 py-2.5">
        <Avatar name={account} />
        <span className="text-sm font-semibold">{account}</span>
      </div>
      {post ? (
        <PostThumb post={post} tone="dark" className="w-full" />
      ) : (
        <div className="grid aspect-square place-items-center bg-neutral-900 px-10 text-center text-sm text-neutral-400">
          {hint}
        </div>
      )}
      <div className="flex items-center gap-4 px-3 pt-3">
        <HeartIcon size={22} />
        <CommentIcon size={22} />
        <SendIcon size={22} />
      </div>
      {caption ? (
        <p className="px-3 pt-2 text-sm leading-snug">
          <span className="font-semibold">{account}</span> {caption}
        </p>
      ) : null}
      <p className="px-3 pt-1.5 text-xs text-neutral-500">{p.viewComments}</p>
    </div>
  );
}

function Comment({ name, text }: { name: string; text: string }) {
  return (
    <div className="flex gap-2.5">
      <Avatar name={name} />
      <div className="min-w-0 text-sm leading-snug">
        <p>
          <span className="font-semibold">{name}</span>{" "}
          <span className="text-xs text-neutral-500">{p.now}</span>
        </p>
        <p className="break-words">{text}</p>
        <p className="mt-1 text-xs font-semibold text-neutral-500">{p.reply}</p>
      </div>
    </div>
  );
}

function CommentsScreen({
  account,
  comment,
  publicReply,
}: {
  account: string;
  comment: string;
  publicReply: string | null;
}) {
  return (
    <div className="px-4">
      <p className="py-3 text-center text-sm font-semibold">{p.commentsTitle}</p>
      <div className="space-y-5">
        <Comment name={p.sampleUser} text={comment} />
        {publicReply ? (
          <div className="ml-10">
            <Comment name={account} text={renderDm(publicReply)} />
          </div>
        ) : (
          <p className="ml-10 text-xs text-neutral-500">{p.noPublicReply}</p>
        )}
      </div>
    </div>
  );
}

function StoryScreen({ account, comment }: { account: string; comment: string }) {
  return (
    <div className="flex h-full flex-col">
      <div className="px-3 pt-1">
        <div className="h-0.5 rounded bg-neutral-700">
          <div className="h-full w-2/5 rounded bg-white" />
        </div>
      </div>
      <div className="flex items-center gap-2.5 px-3 py-3">
        <Avatar name={account} />
        <span className="text-sm font-semibold">{account}</span>
        <span className="text-xs text-neutral-400">{p.now}</span>
      </div>
      <div className="grid flex-1 place-items-center px-8 text-center text-lg font-semibold text-neutral-300">
        {p.yourStory}
      </div>
      <div className="space-y-2 p-3">
        <div className="ml-auto w-fit max-w-[80%] rounded-2xl rounded-br-md bg-blue-600 px-3.5 py-2 text-sm break-words">
          {comment}
        </div>
        <p className="text-right text-xs text-neutral-500">{p.storyReplied}</p>
        <div className="rounded-full border border-neutral-700 px-4 py-2.5 text-sm text-neutral-400">
          {p.sendMessage}
        </div>
      </div>
    </div>
  );
}

function DmScreen({ account, thread }: { account: string; thread: ThreadMessage[] }) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2.5 border-b border-neutral-800 px-3 py-2.5">
        <Avatar name={account} />
        <div>
          <p className="text-sm leading-tight font-semibold">{account}</p>
          <p className="text-xs text-neutral-500">{p.dmTitle}</p>
        </div>
      </div>
      <div className="flex-1 space-y-3 p-3">
        {thread.length === 0 ? (
          <p className="mt-8 text-center text-sm text-neutral-500">{p.dmEmpty}</p>
        ) : (
          thread.map((message, index) =>
            message.from === "person" ? (
              <div
                key={index}
                className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-md bg-blue-600 px-3.5 py-2.5 text-[0.9rem] break-words"
              >
                {message.text}
              </div>
            ) : (
              <div key={index} className="max-w-[88%]">
                <div className="rounded-2xl rounded-bl-md bg-neutral-800 px-3.5 py-2.5 text-[0.9rem] leading-snug break-words whitespace-pre-wrap">
                  {message.text}
                </div>
                {message.button ? (
                  <div className="mt-1.5 truncate rounded-full bg-neutral-800 px-4 py-2 text-center text-sm font-semibold text-blue-400">
                    {message.button}
                  </div>
                ) : null}
              </div>
            ),
          )
        )}
      </div>
    </div>
  );
}

export type PhonePreviewProps = {
  tab: PreviewTab;
  tabs: PreviewTab[];
  onTab: (tab: PreviewTab) => void;
  /** Without the @. */
  account: string;
  post: Post | undefined;
  /** Why there is no post to show: none chosen yet, or the campaign covers any post. */
  postHint: string | null;
  comment: string;
  publicReply: string | null;
  thread: ThreadMessage[];
};

/**
 * The one showpiece of the builder: a phone showing exactly what the person will see. The tabs
 * below it switch between the post, its comments, the story and the DM, so every part of the
 * campaign can be checked without sending anything.
 */
export function PhonePreview(props: PhonePreviewProps) {
  const { tab, tabs, onTab, account, post, postHint, comment, publicReply, thread } = props;
  return (
    <div className="flex flex-col items-center">
      <div className="w-[19rem] rounded-[2.75rem] bg-neutral-900 p-2.5 shadow-card ring-1 ring-black/20">
        <div className="relative h-[35rem] overflow-hidden rounded-[2.25rem] bg-black text-white">
          <div
            aria-hidden
            className="absolute top-2 left-1/2 z-10 h-5 w-24 -translate-x-1/2 rounded-full bg-neutral-900"
          />
          <div
            id="phone-screen"
            role="tabpanel"
            aria-labelledby={`phone-tab-${tab}`}
            className="h-full overflow-y-auto pt-9"
          >
            {tab === "post" ? <PostScreen account={account} post={post} hint={postHint} /> : null}
            {tab === "comments" ? (
              <CommentsScreen account={account} comment={comment} publicReply={publicReply} />
            ) : null}
            {tab === "story" ? <StoryScreen account={account} comment={comment} /> : null}
            {tab === "dm" ? <DmScreen account={account} thread={thread} /> : null}
          </div>
        </div>
      </div>

      <div
        role="tablist"
        aria-label={t.previewTabsLabel}
        className="mt-5 inline-flex rounded-full bg-surface-2 p-1"
      >
        {tabs.map((id) => (
          <button
            key={id}
            id={`phone-tab-${id}`}
            type="button"
            role="tab"
            aria-selected={tab === id}
            aria-controls="phone-screen"
            onClick={() => onTab(id)}
            className={`rounded-full px-3.5 py-1.5 text-sm font-semibold transition-colors ${
              tab === id ? "bg-surface text-fg shadow-card" : "text-muted hover:text-fg"
            }`}
          >
            {t.previewTabs[id]}
          </button>
        ))}
      </div>
      <p className="mt-4 max-w-[19rem] text-center text-sm text-muted">{t.previewNoteLong}</p>
    </div>
  );
}
