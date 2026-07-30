"use client";

import { useEffect, useState } from "react";
import { BeforeAfterPlayer } from "@/components/landing/BeforeAfterPlayer";
import { api } from "@/lib/api";
import { saveBlobDownload } from "@/lib/demo-api";
import { useAppDictionary } from "@/lib/i18n/locale-context";
import { preferStableMediaUrl } from "@/lib/media-url";
import type { Project, Segment } from "@/lib/ui-types";

type DubSummary = Pick<
  Project,
  "id" | "title" | "source_lang" | "target_lang" | "duration_seconds" | "created_at" | "status" | "subtitle_mode"
>;

type Props = {
  open: boolean;
  project: DubSummary | null;
  onClose: () => void;
};

function formatMs(ms: number) {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, "0")}`;
}

function formatDubDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return "—";
  const total = Math.round(seconds);
  const mins = Math.floor(total / 60);
  const secs = total % 60;
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

export function AdminDubDetailModal({ open, project, onClose }: Props) {
  const text = useAppDictionary();
  const [detail, setDetail] = useState<Project | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [sourceUrl, setSourceUrl] = useState<string | null>(null);
  const [outputUrl, setOutputUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    if (!open || !project) {
      setDetail(null);
      setSegments([]);
      setSourceUrl(null);
      setOutputUrl(null);
      setError(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    void (async () => {
      try {
        const [nextProject, nextSegments] = await Promise.all([
          api.projects.get(project.id),
          api.segments.list(project.id),
        ]);
        if (cancelled) return;
        setDetail(nextProject);
        setSegments(nextSegments);

        if (nextProject.source_key) {
          void api.projects
            .sourceUrl(project.id)
            .then(({ url }) => {
              if (!cancelled) {
                setSourceUrl((prev) => preferStableMediaUrl(prev, url));
              }
            })
            .catch(() => undefined);
        }
        if (nextProject.status === "completed") {
          void api.projects
            .outputUrl(project.id)
            .then(({ url }) => {
              if (!cancelled) {
                setOutputUrl((prev) => preferStableMediaUrl(prev, url));
              }
            })
            .catch(() => undefined);
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : text.adminPermissionDenied,
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [open, project, text.adminPermissionDenied]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  if (!open || !project) return null;

  const handleDownload = async () => {
    setDownloading(true);
    setError(null);
    try {
      const blob = await api.projects.downloadFile(project.id);
      await saveBlobDownload(blob, `${project.title}-dubbed.mp4`);
    } catch (err) {
      setError(err instanceof Error ? err.message : text.downloadFinal);
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div
      className="auth-modal-backdrop"
      role="presentation"
      onClick={onClose}
    >
      <div
        className="auth-modal admin-dub-detail-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="admin-dub-detail-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="auth-modal-head">
          <h2 id="admin-dub-detail-title">{text.adminDubDetailTitle}</h2>
          <button
            type="button"
            className="auth-modal-close"
            onClick={onClose}
            aria-label={text.close}
          >
            ×
          </button>
        </div>

        <div className="admin-dub-detail-meta">
          <strong>{project.title}</strong>
          <span>
            {project.source_lang.toUpperCase()} →{" "}
            {project.target_lang.toUpperCase()}
          </span>
          <span>
            {text.dubDuration}: {formatDubDuration(project.duration_seconds)}
          </span>
          <span>{new Date(project.created_at).toLocaleString()}</span>
        </div>

        {loading && <p className="muted">{text.loading}</p>}
        {error && <p className="form-msg err">{error}</p>}

        {!loading && sourceUrl && (
          <div className="admin-dub-detail-player">
            <BeforeAfterPlayer
              beforeSrc={sourceUrl}
              afterSrc={outputUrl ?? ""}
              beforeLabel={text.beforeOriginal}
              afterLabel={text.afterDubbed}
              segments={segments}
              subtitleMode={detail?.subtitle_mode ?? "target"}
            />
          </div>
        )}

        {!loading && segments.length > 0 && (
          <div className="translation-preview-list admin-dub-detail-segments">
            {segments.map((segment, index) => (
              <article key={segment.id} className="translation-preview-item">
                <div className="seg-meta">
                  <span>#{index + 1}</span>
                  <span>
                    {formatMs(segment.start_ms)} – {formatMs(segment.end_ms)}
                  </span>
                </div>
                <p className="translation-preview-source">
                  {segment.source_text}
                </p>
                <p className="translation-preview-target">
                  {segment.target_text}
                </p>
              </article>
            ))}
          </div>
        )}

        <div className="action-row admin-dub-detail-actions">
          {detail?.status === "completed" && (
            <button
              type="button"
              className="btn-primary"
              disabled={downloading}
              onClick={() => void handleDownload()}
            >
              {downloading ? text.loading : text.downloadFinal}
            </button>
          )}
          <button type="button" className="btn-secondary" onClick={onClose}>
            {text.close}
          </button>
        </div>
      </div>
    </div>
  );
}
