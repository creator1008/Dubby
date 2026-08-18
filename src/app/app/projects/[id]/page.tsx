"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { JobProgress } from "@/components/app/JobProgress";
import { SubtitleEditor } from "@/components/app/SubtitleEditor";
import { TranslationPreviewModal } from "@/components/app/TranslationPreviewModal";
import { BeforeAfterPlayer } from "@/components/landing/BeforeAfterPlayer";
import { api, isDemoMode } from "@/lib/api";
import { downloadProjectOutput } from "@/lib/mobile";
import { retranslateLocalSegments } from "@/lib/local-step12";
import { useAppDictionary } from "@/lib/i18n/locale-context";
import { isDubLangCode } from "@/lib/languages";
import { preferStableMediaUrl } from "@/lib/media-url";
import type { Job, Project, Segment, ToneStyle } from "@/lib/ui-types";
import { mergeSegmentVoiceFields } from "@/lib/ui-types";
import {
  applySpeakRateChange,
  ensureSourceEndMs,
  prepareSegmentsForSave,
  videoEndMsFromSegments,
} from "@/lib/speak-rate";

function snapshotSourceTexts(rows: Segment[]) {
  return Object.fromEntries(rows.map((row) => [row.id, row.source_text]));
}

function snapshotTargetTexts(rows: Segment[]) {
  return Object.fromEntries(rows.map((row) => [row.id, row.target_text]));
}

function ProjectEditor() {
  const text = useAppDictionary();
  const projectId = useSearchParams().get("id");
  const [project, setProject] = useState<Project | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [sourceUrl, setSourceUrl] = useState<string | null>(null);
  const [voiceRemovedUrl, setVoiceRemovedUrl] = useState<string | null>(null);
  const [outputUrl, setOutputUrl] = useState<string | null>(null);
  const [translationPreviewOpen, setTranslationPreviewOpen] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [retranslating, setRetranslating] = useState(false);
  const baselineSourceRef = useRef<Record<string, string>>({});
  const baselineTargetRef = useRef<Record<string, string>>({});

  const load = useCallback(async () => {
    if (!projectId) return;
    const [nextProject, nextSegments, nextJobs] = await Promise.all([
      api.projects.get(projectId),
      api.segments.list(projectId),
      api.jobs.list(projectId),
    ]);
    setProject(nextProject);
    setSegments(ensureSourceEndMs(nextSegments));
    if (Object.keys(baselineSourceRef.current).length === 0) {
      baselineSourceRef.current = snapshotSourceTexts(nextSegments);
    }
    if (Object.keys(baselineTargetRef.current).length === 0) {
      baselineTargetRef.current = snapshotTargetTexts(nextSegments);
    }
    setJobs(nextJobs);
    if (nextProject.source_key) {
      void api.projects
        .sourceUrl(projectId)
        .then(({ url }) =>
          setSourceUrl((prev) => preferStableMediaUrl(prev, url)),
        )
        .catch(() => setSourceUrl(null));
      if (
        nextProject.status === "ready_for_edit" ||
        nextProject.status === "completed" ||
        nextProject.status === "dubbing"
      ) {
        void api.projects
          .voiceRemovedUrl(projectId)
          .then(({ url }) =>
            setVoiceRemovedUrl((prev) => preferStableMediaUrl(prev, url)),
          )
          .catch(() => undefined);
      }
    }
    if (nextProject.status === "completed") {
      void api.projects
        .outputUrl(projectId)
        .then(({ url }) =>
          setOutputUrl((prev) => preferStableMediaUrl(prev, url)),
        )
        .catch(() => setOutputUrl(null));
    } else {
      setOutputUrl(null);
    }
  }, [projectId]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void load().catch((err: Error) => setError(err.message));
    }, 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const activeJob = jobs.find((job) => job.status === "queued" || job.status === "running");
  useEffect(() => {
    if (!activeJob) return;
    const timer = window.setInterval(() => {
      void load().catch((err: Error) => setError(err.message));
    }, 2000);
    return () => window.clearInterval(timer);
  }, [activeJob, load]);

  const onSegmentChange = (
    segmentId: string,
    field: "source_text" | "target_text",
    value: string,
  ) => {
    setSegments((current) =>
      current.map((segment) =>
        segment.id === segmentId ? { ...segment, [field]: value } : segment,
      ),
    );
    setMessage(null);
  };

  const save = async () => {
    if (!projectId || !project) return;
    setBusy(true);
    try {
      const videoEndMs = videoEndMsFromSegments(
        segments,
        project.duration_seconds,
      );
      const prepared = prepareSegmentsForSave(
        segments,
        baselineTargetRef.current,
        project.source_lang,
        project.target_lang,
        videoEndMs,
      );
      if (prepared !== segments) {
        setSegments(prepared);
      }
      const next = await api.segments.update(
        projectId,
        prepared.map(({ id, source_text, target_text, end_ms, source_end_ms, speak_speed }) => ({
          id,
          source_text,
          target_text,
          end_ms,
          source_end_ms: source_end_ms ?? end_ms,
          speak_speed:
            typeof speak_speed === "number" && Number.isFinite(speak_speed)
              ? speak_speed
              : 1,
        })),
      );
      const merged = ensureSourceEndMs(mergeSegmentVoiceFields(prepared, next));
      setSegments(merged);
      baselineTargetRef.current = snapshotTargetTexts(merged);
      setMessage("자막을 저장했습니다.");
    } finally {
      setBusy(false);
    }
  };

  const onRetranslate = async () => {
    if (!project || !projectId) return;
    setError(null);
    setMessage(null);
    const changed = segments.filter(
      (segment) =>
        (baselineSourceRef.current[segment.id] ?? segment.source_text) !==
        segment.source_text,
    );
    if (!changed.length) {
      setMessage(text.noSourceTextEdits);
      return;
    }
    setRetranslating(true);
    try {
      if (!isDubLangCode(project.source_lang) || !isDubLangCode(project.target_lang)) {
        throw new Error("지원하지 않는 언어 코드입니다.");
      }
      let saved: Segment[];
      if (isDemoMode) {
        const translations = await retranslateLocalSegments(
          project.source_lang,
          project.target_lang,
          changed.map(({ idx, start_ms, end_ms, source_text }) => ({
            idx,
            start_ms,
            end_ms,
            source_text,
          })),
        );
        const byIdx = new Map(
          translations.map((row) => [row.idx, row.target_text]),
        );
        const next = segments.map((segment) => {
          const target = byIdx.get(segment.idx);
          return target === undefined
            ? segment
            : { ...segment, target_text: target };
        });
        saved = await api.segments.update(
          projectId,
          next.map(({ id, source_text, target_text }) => ({
            id,
            source_text,
            target_text,
          })),
        );
      } else {
        saved = await api.segments.retranslate(
          projectId,
          changed.map(({ id, source_text }) => ({ id, source_text })),
        );
      }
      setSegments(mergeSegmentVoiceFields(segments, saved));
      baselineSourceRef.current = snapshotSourceTexts(saved);
      baselineTargetRef.current = snapshotTargetTexts(saved);
      setMessage(text.retranslateDone);
    } catch (err) {
      setError(err instanceof Error ? err.message : text.retranslate);
    } finally {
      setRetranslating(false);
    }
  };

  const startDub = async () => {
    if (!projectId) return;
    setError(null);
    try {
      await save();
      setOutputUrl(null);
      await api.jobs.create(projectId, "dub");
      window.dispatchEvent(new Event("credits-changed"));
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "더빙을 시작하지 못했습니다.");
    }
  };

  const updateQualitySetting = async (
    patch: Partial<Pick<Project, "tone_style" | "diarization_enabled">>,
  ) => {
    if (!projectId) return;
    try {
      setProject(await api.projects.update(projectId, patch));
      setMessage("품질 설정을 저장했습니다.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "설정을 저장하지 못했습니다.");
    }
  };

  const startLipSync = async () => {
    if (!projectId) return;
    setError(null);
    try {
      await api.jobs.create(projectId, "lipsync");
      window.dispatchEvent(new Event("credits-changed"));
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "프리미엄 립싱크를 시작하지 못했습니다.");
    }
  };

  const download = async () => {
    if (!projectId) return;
    setError(null);
    try {
      await downloadProjectOutput({
        filename: `${project?.title ?? "dubby-output"}-dubbed.mp4`,
        getSignedUrl: async () => {
          const { url } = await api.projects.download(projectId);
          return url;
        },
        getBlob: () => api.projects.downloadFile(projectId),
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "다운로드하지 못했습니다.");
    }
  };

  const downloadOriginal = async () => {
    if (!projectId) return;
    setError(null);
    try {
      await downloadProjectOutput({
        filename: `${project?.title ?? "dubby-output"}-original.mp4`,
        getSignedUrl: async () => {
          const { url } = await api.projects.sourceUrl(projectId);
          return url;
        },
        getBlob: async () => {
          const { url } = await api.projects.sourceUrl(projectId);
          const res = await fetch(url);
          if (!res.ok) throw new Error(`다운로드 실패 (${res.status})`);
          return res.blob();
        },
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "다운로드하지 못했습니다.");
    }
  };

  if (!projectId) return <p className="form-msg err">프로젝트 ID가 없습니다.</p>;
  if (!project) return <p className="muted">{error ?? text.loadingProject}</p>;

  return (
    <>
      <div className="app-hero-row">
        <div>
          <p className="muted" style={{ marginBottom: "0.35rem" }}>
            <Link href="/app">← {text.projects}</Link>
          </p>
          <h1>{project.title}</h1>
          <p className="muted">
            {project.source_lang.toUpperCase()} → {project.target_lang.toUpperCase()} · {text.subtitles}:{" "}
            {project.subtitle_mode}
          </p>
        </div>
        <span className={`status-chip ${project.status}`}>{project.status}</span>
      </div>

      {error && <p className="form-msg err">{error}</p>}
      {activeJob && (
        <>
          <JobProgress job={activeJob} />
          <div className="action-row">
            <button
              className="btn-ghost"
              type="button"
              onClick={() => void api.jobs.cancel(activeJob.id).then(() => {
                window.dispatchEvent(new Event("credits-changed"));
                return load();
              }).catch((err: Error) => setError(err.message))}
            >
              {text.cancelJob}
            </button>
          </div>
        </>
      )}

      <div className="editor-stack">
        <div className="app-panel">
          <h2 className="panel-inline-title">{text.beforeAfter}</h2>
          {sourceUrl ? (
            <BeforeAfterPlayer
              beforeSrc={
                project.status === "completed"
                  ? sourceUrl
                  : (voiceRemovedUrl ?? sourceUrl)
              }
              afterSrc={outputUrl ?? ""}
              beforeLabel={
                project.status === "completed" || !voiceRemovedUrl
                  ? text.beforeOriginal
                  : text.beforeVoiceRemoved
              }
              afterLabel={text.afterDubbed}
              segments={segments}
              subtitleMode={project.subtitle_mode}
              onDownloadBefore={() => void downloadOriginal()}
              onDownloadAfter={() => void download()}
            />
          ) : (
            <p className="muted">{text.noSourceVideo}</p>
          )}
          <TranslationPreviewModal
            open={translationPreviewOpen}
            segments={segments}
            sourceLang={project.source_lang}
            targetLang={project.target_lang}
            onClose={() => setTranslationPreviewOpen(false)}
          />
        </div>

        <div className="app-panel">
          <h2 className="panel-inline-title">{text.dubResult}</h2>
          <div className="row">
            <label>
              {text.tone}
              <select
                value={project.tone_style}
                disabled={Boolean(activeJob)}
                onChange={(e) => void updateQualitySetting({
                  tone_style: e.target.value as ToneStyle,
                })}
              >
                <option value="neutral">Neutral</option>
                <option value="warm">Warm</option>
                <option value="energetic">Energetic</option>
                <option value="serious">Serious</option>
              </select>
            </label>
            <label>
              {text.speakerSeparation}
              <select
                value={project.diarization_enabled ? "multi" : "single"}
                disabled={Boolean(activeJob)}
                onChange={(e) => void updateQualitySetting({
                  diarization_enabled: e.target.value === "multi",
                })}
              >
                <option value="single">{text.singleSpeaker}</option>
                <option value="multi">{text.multiSpeaker}</option>
              </select>
            </label>
          </div>
          <div className="action-row">
            <button
              className="btn-primary"
              type="button"
              disabled={isDemoMode || project.status !== "completed" || Boolean(activeJob)}
              onClick={startLipSync}
            >
              {text.premiumLipSync}
            </button>
          </div>
        </div>

        <div className="app-panel editor-panel">
            <div className="editor-panel-head">
              <h2>{text.subtitleEditor}</h2>
              <p className="muted">{text.reviewThenDub}</p>
            </div>
            <SubtitleEditor
              segments={segments}
              sourceLang={project.source_lang}
              targetLang={project.target_lang}
              disabled={busy || Boolean(activeJob)}
              showSpeakRate={
                project.status === "completed" ||
                segments.some((segment) => Boolean(segment.dubbed_audio_url))
              }
              onChange={onSegmentChange}
              onSpeakSpeedChange={(id, speed) => {
                setSegments((prev) =>
                  applySpeakRateChange(
                    prev,
                    id,
                    speed,
                    videoEndMsFromSegments(prev, project.duration_seconds),
                  ),
                );
              }}
            />
            <div className="action-row editor-actions">
              <button
                type="button"
                className="btn-ghost"
                disabled={busy || Boolean(activeJob)}
                onClick={() => void save().catch((err: Error) => setError(err.message))}
              >
                {text.saveSubtitles}
              </button>
              <button
                type="button"
                className="btn-ghost"
                disabled={segments.length === 0}
                onClick={() => setTranslationPreviewOpen(true)}
              >
                {text.viewTranslation}
              </button>
              <button
                type="button"
                className="btn-ghost"
                disabled={busy || retranslating || Boolean(activeJob) || segments.length === 0}
                onClick={() => void onRetranslate()}
              >
                {retranslating ? text.retranslating : text.retranslate}
              </button>
              <button
                type="button"
                className="btn-primary btn-dub"
                disabled={
                  isDemoMode ||
                  busy ||
                  Boolean(activeJob) ||
                  segments.length === 0 ||
                  (project.status !== "ready_for_edit" &&
                    project.status !== "completed" &&
                    project.status !== "failed")
                }
                onClick={startDub}
              >
                {project.status === "completed"
                  ? text.regenerateDubFile
                  : text.startDubbing}
              </button>
            </div>
            {message && <p className="form-msg ok">{message}</p>}
          </div>
      </div>
    </>
  );
}

export default function ProjectEditorPage() {
  const text = useAppDictionary();
  return (
    <Suspense fallback={<p className="muted">{text.loadingProject}</p>}>
      <ProjectEditor />
    </Suspense>
  );
}
