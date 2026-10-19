"use client";

import { useState } from "react";
import { CheckIcon } from "@/components/icons";
import { PostThumb } from "@/components/post-thumb";
import { Field, inputClass } from "@/components/ui";
import { messages } from "@/lib/messages";
import { postLabel, type Post } from "@/lib/posts";

const t = messages.builder;
const COLLAPSED = 4;

export type PostsQuery = {
  data: Post[] | undefined;
  error: unknown;
  isLoading: boolean;
};

/**
 * Posts as a row of pictures to pick from, with the rest one tap away. A post is recognised by
 * its picture and its caption, so both are shown: the picture to choose, the caption below it
 * once chosen. Without access to Meta, a field takes the post id instead.
 */
export function PostPicker({
  query,
  selected,
  onSelect,
}: {
  query: PostsQuery;
  selected: string;
  onSelect: (id: string) => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const posts = query.data ?? [];
  const manual = Boolean(query.error) || (query.data !== undefined && posts.length === 0);
  const chosen = posts.find((p) => p.id === selected);

  // Folded, show the first few, but never hide the one that is chosen.
  let shown = posts;
  if (!showAll && posts.length > COLLAPSED) {
    shown = posts.slice(0, COLLAPSED);
    if (chosen && !shown.includes(chosen)) shown = [...posts.slice(0, COLLAPSED - 1), chosen];
  }

  return (
    <div className="space-y-3">
      {query.isLoading ? (
        <div role="status" aria-label={t.postsLoading} className="grid grid-cols-4 gap-2">
          {Array.from({ length: COLLAPSED }, (_, i) => (
            <span key={i} className="aspect-square animate-pulse rounded-lg bg-surface-2" />
          ))}
        </div>
      ) : null}
      {query.error ? <p className="text-sm text-muted">{t.postsFailed}</p> : null}
      {query.data && posts.length === 0 ? <p className="text-sm text-muted">{t.postsEmpty}</p> : null}

      {posts.length > 0 ? (
        <>
          <div
            role="radiogroup"
            aria-label={t.pickPost}
            className={`grid gap-2 ${showAll ? "grid-cols-4 sm:grid-cols-6" : "grid-cols-4"}`}
          >
            {shown.map((post) => {
              const isSelected = post.id === selected;
              return (
                <button
                  key={post.id}
                  type="button"
                  role="radio"
                  aria-checked={isSelected}
                  aria-label={t.choosePost(postLabel(post))}
                  title={postLabel(post)}
                  onClick={() => onSelect(post.id)}
                  className={`relative overflow-hidden rounded-lg transition-shadow ${
                    isSelected ? "ring-2 ring-accent" : "ring-1 ring-line hover:ring-muted"
                  }`}
                >
                  <PostThumb post={post} className="w-full" />
                  {isSelected ? (
                    <span className="absolute top-1.5 right-1.5 grid size-5 place-items-center rounded-full bg-accent text-accent-fg">
                      <CheckIcon size={12} strokeWidth={3} />
                    </span>
                  ) : null}
                </button>
              );
            })}
          </div>
          {posts.length > COLLAPSED ? (
            <button
              type="button"
              onClick={() => setShowAll((value) => !value)}
              className="text-sm font-semibold text-accent hover:underline"
            >
              {showAll ? t.showLess : t.showAll}
            </button>
          ) : null}
        </>
      ) : null}

      {selected && !manual ? (
        <p className="flex items-center gap-2 text-sm">
          <span className="shrink-0 text-muted">{t.selectedPost}:</span>
          <span className="min-w-0 truncate font-medium">
            {chosen ? postLabel(chosen) : selected}
          </span>
        </p>
      ) : null}

      {manual ? (
        <Field label={t.postManual} hint={t.postManualHint}>
          {(props) => (
            <input
              {...props}
              inputMode="numeric"
              value={selected}
              onChange={(e) => onSelect(e.target.value.replace(/\D/g, ""))}
              className={inputClass}
            />
          )}
        </Field>
      ) : null}
    </div>
  );
}
