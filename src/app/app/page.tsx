"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, isTransientNetworkError } from "@/lib/api";
import type { Project } from "@/lib/ui-types";
import { useAppDictionary, useLocale } from "@/lib/i18n/locale-context";
import type { Locale } from "@/lib/i18n/dictionaries";

const STATUS_LABELS: Record<Locale, Record<string, string>> = {
  ko: {
    created: "생성됨", uploading: "업로드 중", uploaded: "업로드됨",
    processing: "처리 중", ready_for_edit: "자막 검수", dubbing: "더빙 중",
    completed: "완료", failed: "오류",
  },
  en: {
    created: "Created", uploading: "Uploading", uploaded: "Uploaded",
    processing: "Processing", ready_for_edit: "Subtitle review", dubbing: "Dubbing",
    completed: "Completed", failed: "Failed",
  },
  vi: {
    created: "Đã tạo", uploading: "Đang tải lên", uploaded: "Đã tải lên",
    processing: "Đang xử lý", ready_for_edit: "Duyệt phụ đề", dubbing: "Đang lồng tiếng",
    completed: "Hoàn tất", failed: "Lỗi",
  },
};

export default function AppHomePage() {
  const text = useAppDictionary();
  const { locale } = useLocale();
  const [projects, setProjects] = useState<Project[]>([]);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const boot = async () => {
      let lastErr: unknown;
      for (let attempt = 0; attempt < 4; attempt += 1) {
        try {
          const rows = await api.projects.list();
          if (cancelled) return;
          setProjects(rows);
          setError(null);
          return;
        } catch (err) {
          lastErr = err;
          if (attempt < 3 && isTransientNetworkError(err)) {
            await new Promise((resolve) =>
              window.setTimeout(resolve, 700 * (attempt + 1)),
            );
            continue;
          }
          break;
        }
      }
      if (!cancelled) {
        setError(
          lastErr instanceof Error ? lastErr.message : "불러오지 못했습니다.",
        );
      }
    };
    void boot().finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const onDelete = async (project: Project) => {
    const ok = window.confirm(`「${project.title}」 ${text.deleteConfirm}`);
    if (!ok) return;
    setDeletingId(project.id);
    try {
      await api.projects.remove(project.id);
      setProjects((prev) => prev.filter((p) => p.id !== project.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "삭제하지 못했습니다.");
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <>
      <div className="app-hero-row">
        <div>
          <h1>{text.history}</h1>
          <p className="muted">{text.historyDescription}</p>
        </div>
        <Link href="/app/new" className="btn-primary">
          {text.newDub}
        </Link>
      </div>

      {error && <p className="form-msg err">{error}</p>}
      {loading && <p className="muted">{text.loading}</p>}
      {!loading && projects.length === 0 && (
        <div className="app-panel empty-history">
          <p style={{ margin: "0 0 1rem" }}>{text.noHistory}</p>
          <Link href="/app/new" className="btn-primary">
            {text.firstDub}
          </Link>
        </div>
      )}

      <div className="project-list">
        {projects.map((p) => (
          <div key={p.id} className="project-item-row">
            <Link href={`/app/projects/_/?id=${encodeURIComponent(p.id)}`} className="project-item">
              <div>
                <h3>{p.title}</h3>
                <p>
                  {p.source_lang.toUpperCase()} → {p.target_lang.toUpperCase()} ·{" "}
                  {new Date(p.created_at).toLocaleString()}
                </p>
              </div>
              <span className={`status-chip ${p.status}`}>
                {STATUS_LABELS[locale][p.status] ?? p.status}
              </span>
            </Link>
            <button
              type="button"
              className="btn-delete"
              disabled={deletingId === p.id}
              title={text.deleteHistory}
              onClick={() => onDelete(p)}
            >
              {deletingId === p.id ? "…" : text.delete}
            </button>
          </div>
        ))}
      </div>
    </>
  );
}
