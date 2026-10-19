"use client";

import { useState } from "react";
import { ImageIcon, PlayIcon } from "@/components/icons";
import { isVideo, thumbnailOf, type Post } from "@/lib/posts";

function Photo({ url, className }: { url: string; className: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) return null;
  return (
    // Instagram CDN thumbnails: external and already sized, so nothing to optimize. If one
    // fails to load, the tile below it is what the person sees.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={url}
      alt=""
      loading="lazy"
      onError={() => setFailed(true)}
      className={`absolute inset-0 size-full object-cover ${className}`}
    />
  );
}

/** A square thumbnail of a post. A post without a picture still gets a tile, never a gap. */
export function PostThumb({
  post,
  className = "",
  tone = "theme",
}: {
  post: Post | undefined;
  className?: string;
  /** "dark" is for the phone preview, which stays dark in both themes. */
  tone?: "theme" | "dark";
}) {
  const url = post ? thumbnailOf(post) : null;
  return (
    <span
      className={`relative grid aspect-square shrink-0 place-items-center overflow-hidden ${tone === "dark" ? "bg-neutral-800 text-neutral-500" : "bg-surface-2 text-muted"} ${className}`}
    >
      {post && isVideo(post) ? <PlayIcon size={18} /> : <ImageIcon size={18} />}
      {url ? <Photo key={url} url={url} className="" /> : null}
    </span>
  );
}
