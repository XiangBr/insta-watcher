import type { components } from "@/lib/api-types";
import { dateTime } from "@/lib/format";

export type Post = components["schemas"]["PostOut"];

/** Instagram's CDN only. The API gives https URLs, but the page never trusts that blindly. */
export function thumbnailOf(post: Post): string | null {
  const url = post.thumbnail_url ?? post.media_url ?? null;
  return url && url.startsWith("https://") ? url : null;
}

export function isVideo(post: Post): boolean {
  return post.media_type === "VIDEO" || post.media_type === "REELS";
}

/** First line of the caption, which is what a person recognises a post by. */
export function captionLine(post: Post, max = 90): string {
  const first = (post.caption ?? "").split(/\r?\n/).find((line) => line.trim() !== "") ?? "";
  const text = first.trim().replace(/\s+/g, " ");
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text;
}

/** The caption, or the date when the post has none, so a card is never blank. */
export function postLabel(post: Post): string {
  return captionLine(post) || dateTime(post.timestamp);
}

export function indexPosts(posts: readonly Post[] | undefined): Map<string, Post> {
  return new Map((posts ?? []).map((post) => [post.id, post]));
}
