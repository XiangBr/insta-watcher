"use client";

import Link from "next/link";
import { useState, type ReactNode } from "react";
import useSWR from "swr";
import { BoltIcon, SparkIcon } from "@/components/icons";
import { Button, Card, Notice, Pill } from "@/components/ui";
import { ApiCallError } from "@/lib/api-error";
import { browserApi, unwrap } from "@/lib/api.client";
import type { components } from "@/lib/api-types";
import { campaignHref } from "@/lib/competitors";
import { relativeTime } from "@/lib/format";
import { messages } from "@/lib/messages";
import { Section } from "../insights/insights-sections";

const t = messages.competitors;
const r = t.report;
type Content = components["schemas"]["ReportContent"];

function List({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <Card className="p-4">
      <h3 className="font-semibold">{title}</h3>
      <ul className="mt-2 list-disc space-y-1.5 pl-5 text-sm">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </Card>
  );
}

function Labeled({ label, children }: { label: string; children: ReactNode }) {
  return (
    <p className="text-sm">
      <span className="text-muted">{label}: </span>
      {children}
    </p>
  );
}

function ReportBody({ content }: { content: Content }) {
  // Every list has a default on the backend, so the generated type marks them optional.
  const ideas = content.ideas ?? [];
  const competitors = content.competitors ?? [];
  return (
    <div className="space-y-4">
      {content.summary ? (
        <Card className="p-4">
          <h3 className="font-semibold">{r.summary}</h3>
          <p className="mt-2 text-sm whitespace-pre-line">{content.summary}</p>
        </Card>
      ) : null}
      <div className="grid gap-4 md:grid-cols-2">
        <List title={r.whatWorks} items={content.what_works ?? []} />
        <List title={r.gaps} items={content.gaps ?? []} />
      </div>
      {ideas.length > 0 ? (
        <div>
          <h3 className="mb-2 font-semibold">{r.ideas}</h3>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {ideas.map((idea) => (
              <Card key={idea.title} className="flex flex-col gap-2 p-4">
                <p className="font-semibold">{idea.title}</p>
                {idea.format ? <Labeled label={r.format}>{idea.format}</Labeled> : null}
                {idea.hook ? <Labeled label={r.hook}>“{idea.hook}”</Labeled> : null}
                {idea.why ? <p className="text-sm text-muted">{idea.why}</p> : null}
                {idea.keyword ? (
                  <div className="mt-auto flex flex-wrap items-center gap-2 pt-2">
                    <Pill tone="accent">
                      {r.keyword}: {idea.keyword}
                    </Pill>
                    <Link
                      href={campaignHref(idea.keyword)}
                      className="inline-flex items-center gap-1 text-sm font-semibold text-accent hover:underline"
                    >
                      <BoltIcon size={14} />
                      {r.createCampaign}
                    </Link>
                  </div>
                ) : null}
              </Card>
            ))}
          </div>
        </div>
      ) : null}
      {competitors.length > 0 ? (
        <div>
          <h3 className="mb-2 font-semibold">{r.competitors}</h3>
          <div className="grid gap-4 md:grid-cols-2">
            {competitors.map((c) => (
              <Card key={c.username} className="space-y-2 p-4">
                <p className="font-semibold">@{c.username.replace(/^@/, "")}</p>
                {c.summary ? <p className="text-sm">{c.summary}</p> : null}
                {c.strength ? <Labeled label={r.strength}>{c.strength}</Labeled> : null}
                {(c.technologies ?? []).length > 0 ? (
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-xs text-muted">{r.technologies}:</span>
                    {(c.technologies ?? []).map((tech) => (
                      <span key={tech} className="rounded-md bg-surface-2 px-2 py-0.5 text-xs">
                        {tech}
                      </span>
                    ))}
                  </div>
                ) : null}
              </Card>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function ReportSection() {
  const { data, mutate } = useSWR(
    "competitors-report",
    () => unwrap(browserApi.GET("/api/v1/competitors/report")),
    {
      revalidateOnFocus: false,
      refreshInterval: (latest) => (latest?.running ? 4_000 : 0),
    },
  );
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const running = Boolean(data?.running) || busy;
  const report = data?.report;

  async function generate() {
    setBusy(true);
    setFlash(null);
    try {
      const result = await unwrap(browserApi.POST("/api/v1/competitors/report"));
      if (!result.started) setFlash({ kind: "ok", text: r.busy });
      await mutate();
    } catch (error) {
      setFlash({
        kind: "error",
        text: error instanceof ApiCallError ? error.message : messages.common.loadFailed,
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section
      title={r.title}
      hint={r.hint}
      actions={
        data?.enabled ? (
          <Button variant="primary" onClick={generate} disabled={running}>
            <SparkIcon size={16} className={running ? "animate-pulse" : ""} />
            {running ? r.generating : r.generate}
          </Button>
        ) : null
      }
    >
      <div className="space-y-4">
        {data && !data.enabled ? <p className="text-sm text-muted">{r.disabled}</p> : null}
        {flash ? <Notice kind={flash.kind}>{flash.text}</Notice> : null}
        {report ? (
          <p className="text-xs text-muted">
            {r.meta(report.model, relativeTime(report.finished_at ?? report.created_at), t.usd(report.cost_usd))}
          </p>
        ) : null}
        {report?.status === "FAILED" && report.error ? (
          <Notice kind="error">{r.failed(report.error)}</Notice>
        ) : null}
        {report?.content ? (
          <ReportBody content={report.content} />
        ) : data && !report && data.enabled ? (
          <Card className="p-6 text-sm text-muted">{r.empty}</Card>
        ) : null}
      </div>
    </Section>
  );
}
