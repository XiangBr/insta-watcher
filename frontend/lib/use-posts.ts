"use client";

import useSWR from "swr";
import { browserApi, unwrap } from "@/lib/api.client";

/**
 * Recent posts, shared by the campaign list (thumbnails) and the builder (the picker). Pass
 * false to hold the request back until it is needed: it asks Meta, so it is not free.
 */
export function usePosts(enabled = true) {
  return useSWR(
    enabled ? "posts" : null,
    () => unwrap(browserApi.GET("/api/v1/posts", { params: { query: { limit: 24 } } })),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  );
}
