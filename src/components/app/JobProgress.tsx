"use client";

import type { Job } from "@/lib/ui-types";
import {
  jobKindLabel,
  jobMessageLabel,
  jobStatusLabel,
} from "@/lib/job-labels";
import { useLocale } from "@/lib/i18n/locale-context";

type Props = {
  job: Job;
  title?: string;
};

export function JobProgress({ job, title }: Props) {
  const { locale } = useLocale();
  const pct = Math.round((job.progress || 0) * 100);
  const active = job.status === "queued" || job.status === "running";

  return (
    <div className={`job-progress ${active ? "is-active" : ""}`}>
      <div className="job-progress-head">
        <strong>{title ?? jobKindLabel(job.kind, locale)}</strong>
        <span className="job-progress-pct">{pct}%</span>
      </div>
      <p className="job-progress-meta">
        {jobStatusLabel(job.status, locale)} · {jobMessageLabel(job.message, locale)}
      </p>
      <div className="progress-bar" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <span style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
