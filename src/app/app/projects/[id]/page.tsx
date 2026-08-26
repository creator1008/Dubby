"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { JobProgress } from "@/components/app/JobProgress";
import { SubtitleEditor } from "@/components/app/SubtitleEditor";
import { TranslationPreviewModal } from "@/components/app/TranslationPreviewModal";
import { BeforeAfterPlayer } from "@/components/landing/BeforeAfterPlayer";
import { api, isDemoMode, isTransientNetworkError } from "@/lib/api";
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

function snapshotEmotionTones(rows: Segment[]) {
  return Object.fromEntries(
    rows.map((row) => [row.id, String(row.emotion_tone || "")]),
  );
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
  const baselineEmotionRef = useRef<Record<string, string>>({});
  const hadDubPreviewRef = useRef(false);

  useEffect(() => {
    if (segments.some((segment) => Boolean(segment.dubbed_audio_url))) {
      hadDubPreviewRef.current = true;
    }
  }, [segments]);

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
    if (Object.keys(baselineEmotionRef.current).length === 0) {
      baselineEmotionRef.current = snapshotEmotionTones(nextSegments);
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
    if (nextProject.status === "failed") {
      setError(nextProject.error ?? "작업이 실패했습니다.");
    } else {
      setError(null);
    }
  }, [projectId]);

  useEffect(() => {
    let cancelled = false;
    const boot = async () => {
      for (let attempt = 0; attempt < 5; attempt += 1) {
        try {
          await load();
          return;
        } catch (err) {
          if (cancelled) return;
          if (attempt < 4 && isTransientNetworkError(err)) {
            await new Promise((resolve) =>
              window.setTimeout(resolve, 600 * (attempt + 1)),
            );
            continue;
          }
          setError(err instanceof Error ? err.message : "불러오지 못했습니다.");
          return;
        }
      }
    };
    const timer = window.setTimeout(() => {
      void boot();
    }, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [load]);

  const activeJob = jobs.find((job) => job.status === "queued" || job.status === "running");
  useEffect(() => {
    if (!activeJob) return;
    const timer = window.setInterval(() => {
      void load().catch((err: unknown) => {
        if (isTransientNetworkError(err)) return;
        setError(err instanceof Error ? err.message : "불러오지 못했습니다.");
      });
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

  const onEmotionToneChange = (segmentId: string, tone: ToneStyle) => {
    setSegments((current) =>
      current.map((segment) =>
        segment.id === segmentId
          ? {
              ...segment,
              emotion_tone: tone,
              // Keep clip_speak_speed so save still detects prior dub preview.
              dubbed_audio_url: undefined,
            }
          : segment,
      ),
    );
    setMessage(null);
  };

  const save = async () => {
    if (!projectId || !project) return;
    setBusy(true);
    setError(null);
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
      const projectHasDubPreview =
        project.status === "completed" ||
        hadDubPreviewRef.current ||
        segments.some(
          (segment) =>
            Boolean(segment.dubbed_audio_url) ||
            typeof segment.clip_speak_speed === "number",
        );
      const changedForPreview = prepared.filter((segment) => {
        const hasTextBaseline = Object.prototype.hasOwnProperty.call(
          baselineTargetRef.current,
          segment.id,
        );
        const hasToneBaseline = Object.prototype.hasOwnProperty.call(
          baselineEmotionRef.current,
          segment.id,
        );
        const priorText =
          baselineTargetRef.current[segment.id] ?? segment.target_text;
        const priorTone = baselineEmotionRef.current[segment.id] ?? "";
        const currentTone = String(segment.emotion_tone || "");
        const textChanged = hasTextBaseline && priorText !== segment.target_text;
        const toneChanged =
          hasToneBaseline &&
          priorTone !== currentTone &&
          !(priorTone === "" && currentTone !== "");
        return projectHasDubPreview && (textChanged || toneChanged);
      });
      const next = await api.segments.update(
        projectId,
        prepared.map(
          ({
            id,
            source_text,
            target_text,
            end_ms,
            source_end_ms,
            speak_speed,
            emotion_tone,
          }) => ({
            id,
            source_text,
            target_text,
            end_ms,
            source_end_ms: source_end_ms ?? end_ms,
            speak_speed:
              typeof speak_speed === "number" && Number.isFinite(speak_speed)
                ? speak_speed
                : 1,
            emotion_tone:
              typeof emotion_tone === "string" && emotion_tone.trim()
                ? emotion_tone.trim()
                : undefined,
          }),
        ),
      );
      const merged = ensureSourceEndMs(mergeSegmentVoiceFields(prepared, next));
      setSegments(merged);
      baselineTargetRef.current = snapshotTargetTexts(merged);
      baselineEmotionRef.current = snapshotEmotionTones(merged);
      if (changedForPreview.length && projectHasDubPreview) {
        setMessage(
          "자막을 저장했습니다. 더빙어 ▶ 미리듣기를 누르면 새 감정톤·번역으로 음성이 생성됩니다.",
        );
      } else {
        setMessage("자막을 저장했습니다.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "자막을 저장하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };

  const ensureDubPreview = async (segmentId: string) => {
    if (!projectId) return undefined;
    const row = segments.find((segment) => segment.id === segmentId);
    if (!row) return undefined;
    if (row.dubbed_audio_url) return row.dubbed_audio_url;
    setMessage("미리듣기 음성을 생성하는 중…");
    setError(null);
    try {
      const refreshed = await api.segments.refreshPreview(projectId, [segmentId]);
      const nextRows = ensureSourceEndMs(
        mergeSegmentVoiceFields(segments, refreshed),
      );
      setSegments(nextRows);
      baselineEmotionRef.current = snapshotEmotionTones(nextRows);
      setMessage("미리듣기 음성을 생성했습니다.");
      return nextRows.find((segment) => segment.id === segmentId)?.dubbed_audio_url;
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "미리듣기 음성 생성에 실패했습니다.";
      setError(
        isTransientNetworkError(message)
          ? "미리듣기 음성 생성에 실패했습니다. 잠시 후 ▶ 로 다시 시도해 주세요."
          : message,
      );
      setMessage(null);
      throw err;
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
      baselineEmotionRef.current = snapshotEmotionTones(saved);
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
                value={
                  ["sad", "angry", "whisper", "excited", "energetic", "calm", "cheerful"].includes(
                    project.tone_style,
                  )
                    ? project.tone_style
                    : "calm"
                }
                disabled={Boolean(activeJob)}
                onChange={(e) => void updateQualitySetting({
                  tone_style: e.target.value as ToneStyle,
                })}
              >
                <option value="sad">{text.toneSad}</option>
                <option value="angry">{text.toneAngry}</option>
                <option value="whisper">{text.toneWhisper}</option>
                <option value="excited">{text.toneExcited}</option>
                <option value="energetic">{text.toneEnergetic}</option>
                <option value="calm">{text.toneCalm}</option>
                <option value="cheerful">{text.toneCheerful}</option>
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
                hadDubPreviewRef.current ||
                segments.some(
                  (segment) =>
                    Boolean(segment.dubbed_audio_url) ||
                    typeof segment.clip_speak_speed === "number",
                )
              }
              sourceMediaUrl={sourceUrl}
              defaultEmotionTone={project.tone_style}
              onChange={onSegmentChange}
              onEmotionToneChange={onEmotionToneChange}
              onEnsureDubPreview={(id) => ensureDubPreview(id)}
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
