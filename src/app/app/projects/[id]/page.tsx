"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { JobProgress } from "@/components/app/JobProgress";
import { SubtitleEditor } from "@/components/app/SubtitleEditor";
import { BeforeAfterPlayer } from "@/components/landing/BeforeAfterPlayer";
import { api, isDemoMode } from "@/lib/api";
import { downloadAndShare } from "@/lib/mobile";
import { useAppDictionary } from "@/lib/i18n/locale-context";
import type { Job, Project, Segment, ToneStyle } from "@/lib/ui-types";

function ProjectEditor() {
  const text = useAppDictionary();
  const projectId = useSearchParams().get("id");
  const [project, setProject] = useState<Project | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [sourceUrl, setSourceUrl] = useState<string | null>(null);
  const [outputUrl, setOutputUrl] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!projectId) return;
    const [nextProject, nextSegments, nextJobs] = await Promise.all([
      api.projects.get(projectId),
      api.segments.list(projectId),
      api.jobs.list(projectId),
    ]);
    setProject(nextProject);
    setSegments(nextSegments);
    setJobs(nextJobs);
    if (nextProject.source_key) {
      void api.projects
        .sourceUrl(projectId)
        .then(({ url }) => setSourceUrl(url))
        .catch(() => setSourceUrl(null));
    }
    if (nextProject.status === "completed") {
      void api.projects
        .download(projectId)
        .then(({ url }) => setOutputUrl(url))
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
    if (!projectId) return;
    setBusy(true);
    try {
      setSegments(await api.segments.update(
        projectId,
        segments.map(({ id, source_text, target_text }) => ({
          id,
          source_text,
          target_text,
        })),
      ));
      setMessage("자막을 저장했습니다.");
    } finally {
      setBusy(false);
    }
  };

  const startDub = async () => {
    if (!projectId) return;
    setError(null);
    try {
      await save();
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
    try {
      const { url } = await api.projects.download(projectId);
      await downloadAndShare(url, `${project?.title ?? "dubby-output"}.mp4`);
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
      {project.quality_warnings.length > 0 && (
        <div className="app-panel" role="status">
          <strong>{text.qualityWarning}</strong>
          <ul>
            {project.quality_warnings.map((warning) => <li key={warning}>{warning}</li>)}
          </ul>
        </div>
      )}
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
              beforeSrc={sourceUrl}
              afterSrc={outputUrl ?? ""}
              beforeLabel={text.beforeOriginal}
              afterLabel={text.afterDubbed}
              segments={segments}
              subtitleMode={project.subtitle_mode}
            />
          ) : (
            <p className="muted">{text.noSourceVideo}</p>
          )}
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
              disabled={project.status !== "completed"}
              onClick={download}
            >
              {text.downloadDub}
            </button>
            <button
              className="btn-primary"
              type="button"
              disabled={isDemoMode || project.status !== "completed" || Boolean(activeJob)}
              onClick={startLipSync}
            >
              {text.premiumLipSync}
            </button>
            <button type="button" className="btn-ghost" onClick={() => void load()}>
              {text.refresh}
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
            onChange={onSegmentChange}
          />
          <div className="action-row editor-actions">
            <button type="button" className="btn-ghost" disabled={busy || Boolean(activeJob)} onClick={() => void save().catch((err: Error) => setError(err.message))}>
              {text.saveSubtitles}
            </button>
            <button type="button" className="btn-primary btn-dub" disabled={isDemoMode || busy || Boolean(activeJob) || segments.length === 0} onClick={startDub}>
              {isDemoMode ? text.continueAfterVerification : text.startDubbing}
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
