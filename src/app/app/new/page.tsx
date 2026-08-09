"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { FileUploader } from "@/components/app/FileUploader";
import { JobProgress } from "@/components/app/JobProgress";
import { SubtitleEditor } from "@/components/app/SubtitleEditor";
import { TranslationPreviewModal } from "@/components/app/TranslationPreviewModal";
import { BeforeAfterPlayer } from "@/components/landing/BeforeAfterPlayer";
import { ApiError, api, isDemoMode, pingApi, uploadSourceFile } from "@/lib/api";
import { formatPipelineError } from "@/lib/job-labels";
import { useVoiceConsent } from "@/lib/consent";
import { demoApi } from "@/lib/demo-api";
import { formatQualityWarning, preferStableMediaUrl } from "@/lib/media-url";
import {
  extractLocalStep12,
  extractLocalStep12FromUrl,
  generateLocalDubVoice,
  renderLocalDubVideo,
  retranslateLocalSegments,
} from "@/lib/local-step12";
import { useAppDictionary, useLocale } from "@/lib/i18n/locale-context";
import { LANG_CODES, LANG_LABELS, isDubLangCode } from "@/lib/languages";
import type {
  Job,
  LangCode,
  Project,
  Segment,
  SubtitleMode,
  ToneStyle,
  UserVoice,
} from "@/lib/ui-types";

const MAX_SPEAKER_VOICES = 6;

function snapshotSourceTexts(rows: Segment[]) {
  return Object.fromEntries(rows.map((row) => [row.id, row.source_text]));
}

function speakerLabel(template: string, n: number) {
  return template.replace("{n}", String(n));
}

function titleCaseWords(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .trim()
    .replace(/\b\w/g, (ch) => ch.toUpperCase());
}

function lookupVoiceLabel(
  text: Record<string, string>,
  prefix: string,
  value: string | null | undefined,
): string {
  const raw = (value || "").trim();
  if (!raw) return "";
  const key = `${prefix}_${raw.toLowerCase().replace(/\s+/g, "_")}`;
  return text[key] || titleCaseWords(raw);
}

function formatVoiceOption(
  voice: UserVoice,
  text: Record<string, string>,
): string {
  const nickname = (voice.nickname || voice.name || "").trim() || "—";
  const parts = [
    lookupVoiceLabel(text, "voiceLang", voice.language),
    lookupVoiceLabel(text, "voiceGender", voice.gender),
    lookupVoiceLabel(text, "voiceAccent", voice.accent),
  ].filter(Boolean);
  return parts.length ? `${nickname} · ${parts.join(" · ")}` : nickname;
}

export default function NewDubPage() {
  const text = useAppDictionary();
  const { locale } = useLocale();
  const [title, setTitle] = useState("");
  const [sourceLang, setSourceLang] = useState<LangCode>("ko");
  const [targetLang, setTargetLang] = useState<LangCode>("en");
  const [subtitleMode, setSubtitleMode] = useState<SubtitleMode>("none");
  const [toneStyle, setToneStyle] = useState<ToneStyle>("neutral");
  const [diarizationEnabled, setDiarizationEnabled] = useState(false);
  const [voiceMode, setVoiceMode] = useState<"voice_box" | "auto_clone">("voice_box");
  const [boxVoices, setBoxVoices] = useState<UserVoice[]>([]);
  const [speakerVoiceIds, setSpeakerVoiceIds] = useState<string[]>([""]);
  const [file, setFile] = useState<File | null>(null);
  const [inputMode, setInputMode] = useState<"file" | "url">("file");
  const [mediaUrl, setMediaUrl] = useState("");
  const [uploadPct, setUploadPct] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [localStage, setLocalStage] = useState<string | null>(null);
  const [localRunId, setLocalRunId] = useState<string | null>(null);

  const [project, setProject] = useState<Project | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [sourceUrl, setSourceUrl] = useState<string | null>(null);
  const [voiceRemovedUrl, setVoiceRemovedUrl] = useState<string | null>(null);
  const [outputUrl, setOutputUrl] = useState<string | null>(null);
  const [translationPreviewOpen, setTranslationPreviewOpen] = useState(false);

  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retranslating, setRetranslating] = useState(false);
  const baselineSourceRef = useRef<Record<string, string>>({});
  const voiceConsent = useVoiceConsent();

  useEffect(() => {
    void api.voices.box
      .list()
      .then((rows) => {
        setBoxVoices(rows);
        setSpeakerVoiceIds((prev) => {
          if (prev.some(Boolean) || rows.length === 0) return prev;
          return [rows[0].elevenlabs_voice_id];
        });
      })
      .catch(() => setBoxVoices([]));
  }, []);

  const setSpeakerMode = (multi: boolean) => {
    setDiarizationEnabled(multi);
    setSpeakerVoiceIds((prev) => {
      const first = prev[0] || "";
      if (!multi) return [first];
      if (prev.length >= 2) return prev;
      return [first, ""];
    });
  };

  const selectedDubVoiceIds = speakerVoiceIds
    .map((id) => id.trim())
    .filter(Boolean);

  const activeJob = jobs.find(
    (job) => job.status === "queued" || job.status === "running",
  );

  const refresh = useCallback(async () => {
    if (!project) return;
    const [nextProject, nextJobs] = await Promise.all([
      api.projects.get(project.id),
      api.jobs.list(project.id),
    ]);
    setProject(nextProject);
    setJobs(nextJobs);
    if (nextProject.status === "ready_for_edit" || nextProject.status === "completed") {
      const nextSegments = await api.segments.list(project.id);
      setSegments(nextSegments);
      if (Object.keys(baselineSourceRef.current).length === 0) {
        baselineSourceRef.current = snapshotSourceTexts(nextSegments);
      }
      void api.projects
        .voiceRemovedUrl(project.id)
        .then(({ url }) =>
          setVoiceRemovedUrl((prev) => preferStableMediaUrl(prev, url)),
        )
        .catch(() => undefined);
    }
    if (nextProject.status === "completed" && !outputUrl) {
      const { url } = await api.projects.download(project.id);
      setOutputUrl((prev) => preferStableMediaUrl(prev, url));
      window.dispatchEvent(new Event("credits-changed"));
    }
  }, [project, outputUrl]);

  useEffect(() => {
    if (!activeJob) return;
    const timer = window.setInterval(() => {
      void refresh().catch((err: Error) => setError(err.message));
    }, 2000);
    return () => window.clearInterval(timer);
  }, [activeJob, refresh]);

  // Step 1 — 파일선택 및 자막추출
  const validateExtractInput = () => {
    const trimmedUrl = mediaUrl.trim();
    if (inputMode === "file" && !file) {
      setError(text.selectVideoFile);
      return null;
    }
    if (inputMode === "url" && !trimmedUrl) {
      setError(text.enterVideoUrl);
      return null;
    }
    if (!voiceConsent.accepted) {
      setError(text.acceptConsent);
      return null;
    }
    if (sourceLang === targetLang) {
      setError(text.sameLanguages);
      return null;
    }
    if (voiceMode === "voice_box") {
      const requiredSlots = diarizationEnabled
        ? Math.max(2, speakerVoiceIds.length)
        : 1;
      const filled = speakerVoiceIds.slice(0, requiredSlots).filter((id) => id.trim());
      if (filled.length < requiredSlots || boxVoices.length === 0) {
        setError(
          boxVoices.length === 0 ? text.voiceSelectEmpty : text.voiceSelectRequired,
        );
        return null;
      }
    }
    return trimmedUrl;
  };

  const runExtract = async (trimmedUrl: string) => {
    if (!isDemoMode) {
      setLocalStage(text.checkingApi);
      await pingApi();
    }
    setLocalStage(text.preparingProject);
    const projectTitle =
      title.trim() ||
      (inputMode === "file" && file
        ? file.name
        : trimmedUrl.slice(0, 80) || text.newDub);
    const created = await api.projects.create({
      title: projectTitle,
      source_lang: sourceLang,
      target_lang: targetLang,
      subtitle_mode: subtitleMode,
      tone_style: toneStyle,
      diarization_enabled: diarizationEnabled,
      voice_mode: voiceMode,
      pipeline_version: "2.0",
      dub_voice_ids: voiceMode === "voice_box" ? selectedDubVoiceIds : [],
    });
    if (inputMode === "file" && file) {
      setLocalStage(text.uploading);
      await uploadSourceFile(created.id, file, (pct) => {
        setUploadPct(pct);
        setLocalStage(`${text.uploading} ${pct}%`);
      });
    } else {
      setLocalStage(text.fetchingLink);
      setUploadPct(20);
      await api.projects.sourceFromUrl(created.id, trimmedUrl);
      // Ingest runs in the background (tunnel-safe). Poll until ready.
      try {
        const ready = await api.projects.waitUntilSourceReady(created.id, {
          onTick: (elapsedMs) => {
            setUploadPct(Math.min(85, 25 + Math.floor(elapsedMs / 3000)));
            setLocalStage(text.fetchingLink);
          },
        });
        if (ready.status === "failed") {
          throw new Error(ready.error || text.fetchingLinkFailed);
        }
      } catch (err) {
        if (err instanceof ApiError && err.status === 408) {
          throw new Error(text.fetchingLinkTimeout);
        }
        if (err instanceof ApiError && err.status === 400) {
          throw new Error(err.message || text.fetchingLinkFailed);
        }
        throw err;
      }
      setUploadPct(90);
    }

    let nextSegments: Segment[] = [];
    let nextRunId: string | null = null;
    let nextSourceUrl: string | null = null;

    if (isDemoMode) {
      setLocalStage(text.localExtractStage);
      const result =
        inputMode === "url"
          ? await extractLocalStep12FromUrl(
              trimmedUrl,
              sourceLang,
              targetLang,
              diarizationEnabled,
            )
          : await extractLocalStep12(
              file!,
              sourceLang,
              targetLang,
              diarizationEnabled,
            );
      nextSegments = await demoApi.applyStep12(created.id, result);
      baselineSourceRef.current = snapshotSourceTexts(nextSegments);
      nextRunId = result.run_id;
      nextSourceUrl = result.source_url;
      setSegments(nextSegments);
      setLocalRunId(nextRunId);
      setSourceUrl(nextSourceUrl);
      setLocalStage(null);
    } else {
      setLocalStage(text.batchStageTranscribe);
      await api.jobs.create(created.id, "transcribe");
    }

    const [nextProject, nextJobs] = await Promise.all([
      api.projects.get(created.id),
      api.jobs.list(created.id),
    ]);
    setProject(nextProject);
    setJobs(nextJobs);
    if (!isDemoMode) {
      // JobProgress shows the live job; avoid a stuck "checking API" banner.
      setLocalStage(null);
    }
    if (!nextSourceUrl) {
      void api.projects
        .sourceUrl(created.id)
        .then(({ url }) => setSourceUrl(url))
        .catch(() => undefined);
    }
    return {
      project: nextProject,
      segments: nextSegments,
      localRunId: nextRunId,
      sourceUrl: nextSourceUrl,
    };
  };

  const onExtract = async (e: FormEvent) => {
    e.preventDefault();
    const trimmedUrl = validateExtractInput();
    if (trimmedUrl === null) return;
    setUploading(true);
    setUploadPct(0);
    setError(null);
    try {
      await runExtract(trimmedUrl);
    } catch (err) {
      setLocalStage(null);
      setError(err instanceof Error ? err.message : text.uploadFailed);
    } finally {
      setUploading(false);
    }
  };

  const runLocalDubVoice = async (
    currentProject: Project,
    rows: Segment[],
    runId: string,
  ) => {
    const speakable = rows
      .filter((segment) => segment.target_text.trim())
      .map((segment) => ({
        idx: segment.idx,
        target_text: segment.target_text.trim(),
      }));
    if (!speakable.length) {
      throw new Error("더빙할 번역 텍스트가 없습니다.");
    }
    await demoApi.assertDubCredits(currentProject.id);
    setLocalStage("선택 목소리로 ElevenLabs 더빙 음성 생성 중");
    const outputs = await generateLocalDubVoice(
      runId,
      speakable,
      toneStyle,
      currentProject.dub_voice_ids ?? selectedDubVoiceIds,
    );
    const nextSegments = await demoApi.applyDubVoice(currentProject.id, outputs);
    setSegments(nextSegments);
    window.dispatchEvent(new Event("credits-changed"));
    return nextSegments;
  };

  const runLocalRender = async (
    currentProject: Project,
    rows: Segment[],
    runId: string,
  ) => {
    setLocalStage(
      "언어 인식 구간만 보이스 제거 → 0.2초 보정 → 비언어·배경음 보존 → 영상 합성 중",
    );
    const result = await renderLocalDubVideo(
      runId,
      rows.map(({ idx, start_ms, end_ms, source_text, target_text }) => ({
        idx,
        start_ms,
        end_ms,
        source_text,
        target_text,
      })),
      currentProject.subtitle_mode,
    );
    setSourceUrl(result.source_url);
    setOutputUrl(`${result.output_url.split("?")[0]}?t=${Date.now()}`);
    const nextProject = await demoApi.applyRender(currentProject.id, result);
    setProject(nextProject);
    return { project: nextProject, result };
  };

  const waitForServerJob = async (
    projectId: string,
    kind: "transcribe" | "dub",
    stageLabel: string,
  ) => {
    setLocalStage(stageLabel);
    const started = Date.now();
    const timeoutMs = 45 * 60 * 1000;
    let networkFailStreak = 0;
    while (Date.now() - started < timeoutMs) {
      try {
        const [nextProject, nextJobs] = await Promise.all([
          api.projects.get(projectId),
          api.jobs.list(projectId),
        ]);
        networkFailStreak = 0;
        setError(null);
        setProject(nextProject);
        setJobs(nextJobs);
        if (nextProject.source_key) {
          void api.projects
            .sourceUrl(projectId)
            .then(({ url }) =>
              setSourceUrl((prev) => preferStableMediaUrl(prev, url)),
            )
            .catch(() => undefined);
        }
        const job = nextJobs.find((row) => row.kind === kind);
        if (job?.status === "failed" || nextProject.status === "failed") {
          throw new Error(
            formatPipelineError(
              job?.error || nextProject.error || `${kind} 작업이 실패했습니다.`,
            ),
          );
        }
        if (kind === "transcribe") {
          if (
            job?.status === "completed" ||
            nextProject.status === "ready_for_edit" ||
            nextProject.status === "completed"
          ) {
            const nextSegments = await api.segments.list(projectId);
            setSegments(nextSegments);
            baselineSourceRef.current = snapshotSourceTexts(nextSegments);
            void api.projects
              .voiceRemovedUrl(projectId)
              .then(({ url }) =>
                setVoiceRemovedUrl((prev) => preferStableMediaUrl(prev, url)),
              )
              .catch(() => undefined);
            return { project: nextProject, segments: nextSegments };
          }
        } else if (
          job?.status === "completed" ||
          nextProject.status === "completed"
        ) {
          if (nextProject.status === "completed") {
            const { url } = await api.projects.download(projectId);
            setOutputUrl((prev) => preferStableMediaUrl(prev, url));
          }
          const nextSegments = await api.segments
            .list(projectId)
            .catch(() => segments);
          setSegments(nextSegments);
          return { project: nextProject, segments: nextSegments };
        }
        const progress =
          typeof job?.progress === "number"
            ? ` ${Math.round(job.progress * 100)}%`
            : "";
        setLocalStage(
          `${stageLabel}${progress}${job?.message ? ` · ${job.message}` : ""}`,
        );
      } catch (err) {
        // Cloudflare quick tunnels briefly drop; keep polling instead of failing the job.
        const isNetwork =
          (err instanceof ApiError && err.status === 0) ||
          (err instanceof TypeError) ||
          (err instanceof Error && /Failed to fetch|네트워크|연결할 수 없습니다/i.test(err.message));
        if (!isNetwork) throw err;
        networkFailStreak += 1;
        if (networkFailStreak >= 15) throw err;
        setLocalStage(`${stageLabel} · 연결 재시도 중…`);
      }
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
    }
    throw new Error(`${kind} 작업 대기 시간이 초과되었습니다.`);
  };

  const onBatchCreate = async () => {
    const trimmedUrl = validateExtractInput();
    if (trimmedUrl === null) return;
    setUploading(true);
    setUploadPct(0);
    setError(null);
    setMessage(null);
    setOutputUrl(null);
    try {
      if (isDemoMode) {
        const extracted = await runExtract(trimmedUrl);
        if (!extracted.localRunId) {
          throw new Error("로컬 추출 작업 ID가 없습니다. 파일을 다시 추출해 주세요.");
        }
        if (!extracted.segments.length) {
          throw new Error(text.noSubtitlesYet);
        }
        const dubbed = await runLocalDubVoice(
          extracted.project,
          extracted.segments,
          extracted.localRunId,
        );
        const { result } = await runLocalRender(
          extracted.project,
          dubbed,
          extracted.localRunId,
        );
        setMessage(
          result.warnings.length
            ? `${text.batchCreateDone} ${result.warnings.join(" ")}`
            : text.batchCreateDone,
        );
        return;
      }

      const extracted = await runExtract(trimmedUrl);
      const transcribed = await waitForServerJob(
        extracted.project.id,
        "transcribe",
        text.batchStageTranscribe,
      );
      if (!transcribed.segments.length) {
        throw new Error(text.noSubtitlesYet);
      }
      setLocalStage(text.batchStageDub);
      await api.jobs.create(extracted.project.id, "dub");
      window.dispatchEvent(new Event("credits-changed"));
      const dubbed = await waitForServerJob(
        extracted.project.id,
        "dub",
        text.batchStageDub,
      );
      const warnings = dubbed.project.quality_warnings ?? [];
      const warningText = warnings
        .map((code) => formatQualityWarning(code, locale))
        .join(" ");
      setMessage(
        warningText
          ? `${text.batchCreateDone} ${warningText}`
          : text.batchCreateDone,
      );
    } catch (err) {
      setLocalStage(null);
      setError(err instanceof Error ? err.message : text.uploadFailed);
    } finally {
      setUploading(false);
      setLocalStage(null);
    }
  };

  // Step 2 — 자막 검증·수정 (최종 영상 생성 후에도 수정·재생성 가능)
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
    // Subtitle edits invalidate the previous final video until voice+render rerun.
    if (outputUrl) setOutputUrl(null);
  };

  const saveSegments = async () => {
    if (!project) return segments;
    const next = await api.segments.update(
      project.id,
      segments.map(({ id, source_text, target_text }) => ({
        id,
        source_text,
        target_text,
      })),
    );
    setSegments(next);
    setMessage("자막을 저장했습니다.");
    return next;
  };

  const onRetranslate = async () => {
    if (!project) return;
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
          project.id,
          next.map(({ id, source_text, target_text }) => ({
            id,
            source_text,
            target_text,
          })),
        );
      } else {
        saved = await api.segments.retranslate(
          project.id,
          changed.map(({ id, source_text }) => ({ id, source_text })),
        );
      }
      setSegments(saved);
      baselineSourceRef.current = snapshotSourceTexts(saved);
      if (outputUrl) setOutputUrl(null);
      setMessage(text.retranslateDone);
    } catch (err) {
      setError(err instanceof Error ? err.message : text.retranslate);
    } finally {
      setRetranslating(false);
    }
  };

  const onSubtitleModeChange = async (mode: SubtitleMode) => {
    setSubtitleMode(mode);
    if (outputUrl) setOutputUrl(null);
    if (!project) return;
    try {
      setProject(await api.projects.update(project.id, { subtitle_mode: mode }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "자막 설정을 저장하지 못했습니다.");
    }
  };

  const onCreateDub = async () => {
    if (!project) return;
    setError(null);
    setOutputUrl(null);
    setLocalStage(null);
    try {
      const saved = await saveSegments();
      const rows = saved ?? segments;
      if (isDemoMode) {
        if (!localRunId) {
          throw new Error("로컬 추출 작업 ID가 없습니다. 파일을 다시 추출해 주세요.");
        }
        await runLocalDubVoice(project, rows, localRunId);
        setLocalStage(null);
        setMessage(
          "더빙 음성을 생성했습니다. 구간을 확인한 뒤 필요하면 자막을 수정하고 다시 생성하거나, 최종 영상을 만드세요.",
        );
        return;
      }
      setLocalStage(text.batchStageDub);
      await api.jobs.create(project.id, "dub");
      window.dispatchEvent(new Event("credits-changed"));
      await refresh();
      setLocalStage(null);
    } catch (err) {
      setLocalStage(null);
      setError(err instanceof Error ? err.message : "더빙을 시작하지 못했습니다.");
    }
  };

  const onRenderDub = async () => {
    if (!project || !localRunId) return;
    setError(null);
    try {
      const saved = await saveSegments();
      const rows = saved ?? segments;
      const { result } = await runLocalRender(project, rows, localRunId);
      setMessage(
        result.warnings.length
          ? `최종 영상을 생성했습니다. ${result.warnings.join(" ")}`
          : "최종 더빙 영상을 생성했습니다.",
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "최종 영상을 생성하지 못했습니다.");
    } finally {
      setLocalStage(null);
    }
  };

  const editorLocked =
    uploading || Boolean(activeJob) || Boolean(localStage);
  const canEdit = segments.length > 0;
  const hasDubVoice =
    isDemoMode &&
    segments.some((segment) => Boolean(segment.dubbed_audio_url));
  const canRegenerateDub = hasDubVoice || Boolean(outputUrl);

  return (
    <>
      <div className="app-hero-row">
        <div>
          <h1>{text.newDub}</h1>
          <p className="muted">
            {text.newDubDescription}
          </p>
        </div>
        {project && (
          <span className={`status-chip ${project.status}`}>{project.status}</span>
        )}
      </div>

      {error && <p className="form-msg err">{error}</p>}
      {project && (project.quality_warnings?.length ?? 0) > 0 && (
        <div className="app-panel" role="status">
          <strong>{text.qualityWarning}</strong>
          <ul style={{ margin: "0.5rem 0 0", paddingLeft: "1.2rem" }}>
            {project.quality_warnings.map((warning) => (
              <li key={warning}>{formatQualityWarning(warning, locale)}</li>
            ))}
          </ul>
        </div>
      )}

      {/* 1. 파일선택 및 자막추출 */}
      {!project && (
        <form className="app-panel app-form" onSubmit={onExtract}>
          <h2 className="panel-inline-title">{text.fileAndSubtitle}</h2>
          <label>
            {text.projectName}
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={text.projectPlaceholder}
              disabled={uploading}
            />
          </label>

          <div className="row">
            <label>
              {text.sourceLanguage}
              <select
                value={sourceLang}
                disabled={uploading}
                onChange={(e) => setSourceLang(e.target.value as LangCode)}
              >
                {LANG_CODES.map((code) => (
                  <option key={code} value={code}>
                    {LANG_LABELS[code]}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {text.targetLanguage}
              <select
                value={targetLang}
                disabled={uploading}
                onChange={(e) => setTargetLang(e.target.value as LangCode)}
              >
                {LANG_CODES.map((code) => (
                  <option key={code} value={code}>
                    {LANG_LABELS[code]}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {text.subtitleLanguage}
              <select
                value={subtitleMode}
                disabled={uploading}
                onChange={(e) => setSubtitleMode(e.target.value as SubtitleMode)}
              >
                <option value="none">{text.noSubtitles}</option>
                <option value="source">{text.sourceSubtitles}</option>
                <option value="target">{text.targetSubtitles}</option>
              </select>
            </label>
            <label>
              {text.tone}
              <select
                value={toneStyle}
                disabled={uploading}
                onChange={(e) => setToneStyle(e.target.value as ToneStyle)}
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
                value={diarizationEnabled ? "multi" : "single"}
                disabled={uploading}
                onChange={(e) => setSpeakerMode(e.target.value === "multi")}
              >
                <option value="single">{text.singleSpeaker}</option>
                <option value="multi">{text.multiSpeaker}</option>
              </select>
            </label>
          </div>

          <div className="voice-select-block">
            <div className="voice-select-head">
              <strong>{text.voiceSelect}</strong>
              <a className="voice-settings-link" href="/app/voice-settings">
                {text.voiceSettingsLink}
              </a>
            </div>
            <div
              className="input-mode-toggle"
              role="group"
              aria-label={text.voiceSelect}
            >
              <button
                type="button"
                className={voiceMode === "voice_box" ? "is-active" : undefined}
                disabled={uploading}
                onClick={() => setVoiceMode("voice_box")}
              >
                {text.voiceModeMyBox}
              </button>
              <button
                type="button"
                className={voiceMode === "auto_clone" ? "is-active" : undefined}
                disabled={uploading}
                onClick={() => setVoiceMode("auto_clone")}
              >
                {text.voiceModeAutoClone}
              </button>
            </div>
            {voiceMode === "auto_clone" ? (
              <p className="muted">{text.voiceModeAutoCloneHelp}</p>
            ) : boxVoices.length === 0 ? (
              <p className="muted">{text.voiceSelectEmpty}</p>
            ) : (
              <div className="voice-select-slots">
                {speakerVoiceIds.map((voiceId, index) => (
                  <label key={`speaker-voice-${index}`}>
                    {speakerLabel(text.voiceSelectSpeaker, index + 1)}
                    <select
                      value={voiceId}
                      disabled={uploading}
                      onChange={(e) => {
                        const next = [...speakerVoiceIds];
                        next[index] = e.target.value;
                        setSpeakerVoiceIds(next);
                      }}
                    >
                      <option value="">{text.voiceSelectPlaceholder}</option>
                      {boxVoices.map((voice) => (
                        <option
                          key={voice.id}
                          value={voice.elevenlabs_voice_id}
                        >
                          {formatVoiceOption(voice, text)}
                        </option>
                      ))}
                    </select>
                  </label>
                ))}
              </div>
            )}
            {voiceMode === "voice_box" && diarizationEnabled && boxVoices.length > 0 && (
              <div className="voice-select-actions">
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={
                    uploading || speakerVoiceIds.length >= MAX_SPEAKER_VOICES
                  }
                  onClick={() =>
                    setSpeakerVoiceIds((prev) =>
                      prev.length >= MAX_SPEAKER_VOICES ? prev : [...prev, ""],
                    )
                  }
                >
                  {text.voiceSelectAddSpeaker}
                </button>
                {speakerVoiceIds.length > 2 && (
                  <button
                    type="button"
                    className="btn-ghost"
                    disabled={uploading}
                    onClick={() =>
                      setSpeakerVoiceIds((prev) => prev.slice(0, -1))
                    }
                  >
                    {text.voiceSelectRemoveSpeaker}
                  </button>
                )}
              </div>
            )}
          </div>

          <div className="input-mode-toggle" role="group" aria-label={text.inputMode}>
            <button
              type="button"
              className={inputMode === "file" ? "is-active" : undefined}
              disabled={uploading}
              onClick={() => setInputMode("file")}
            >
              {text.inputModeFile}
            </button>
            <button
              type="button"
              className={inputMode === "url" ? "is-active" : undefined}
              disabled={uploading}
              onClick={() => setInputMode("url")}
            >
              {text.inputModeUrl}
            </button>
          </div>

          {inputMode === "file" ? (
            <FileUploader file={file} onFile={setFile} disabled={uploading} />
          ) : (
            <label>
              {text.videoLink}
              <input
                type="url"
                value={mediaUrl}
                onChange={(e) => setMediaUrl(e.target.value)}
                placeholder={text.videoLinkPlaceholder}
                disabled={uploading}
                autoComplete="off"
              />
              <span className="field-hint">{text.videoLinkHint}</span>
            </label>
          )}

          <label className="consent-row">
            <input
              type="checkbox"
              checked={voiceConsent.accepted}
              disabled={uploading}
              onChange={(event) => voiceConsent.setAccepted(event.target.checked)}
            />
            <span>
              {text.consent}
            </span>
          </label>

          {uploading && (
            <div className="upload-progress-wrap">
              <div className="upload-progress-head">
                <strong>{inputMode === "url" ? text.fetchingLink : text.fileUpload}</strong>
                <span>{uploadPct}%</span>
              </div>
              <div className="progress-bar" role="progressbar" aria-valuenow={uploadPct}>
                <span style={{ width: `${uploadPct}%` }} />
              </div>
            </div>
          )}

          <div className="action-row editor-actions">
            <button
              className="btn-primary"
              type="submit"
              disabled={
                uploading ||
                !voiceConsent.accepted ||
                (inputMode === "file" ? !file : !mediaUrl.trim())
              }
            >
              {uploading
                ? text.uploading
                : inputMode === "url"
                  ? text.linkAndExtract
                  : text.uploadAndExtract}
            </button>
            <button
              className="btn-primary"
              type="button"
              title={text.batchCreateDubHelp}
              disabled={
                uploading ||
                !voiceConsent.accepted ||
                (inputMode === "file" ? !file : !mediaUrl.trim())
              }
              onClick={() => void onBatchCreate()}
            >
              {uploading ? text.batchCreating : text.batchCreateDub}
            </button>
          </div>
        </form>
      )}

      {activeJob && <JobProgress job={activeJob} />}
      {localStage && (
        <div className="job-progress is-active" role="status">
          <div className="job-progress-head">
            <strong>{text.localProcessing}</strong>
          </div>
          <p className="job-progress-meta">{localStage}</p>
          <div className="progress-bar">
            <span style={{ width: "65%" }} />
          </div>
        </div>
      )}

      {project && (
        <div className="editor-stack">
          {/* 2. 자막 검증·더빙 (최종 영상 후에도 수정·음성 재생성 가능) */}
          {canEdit && (
            <div className="app-panel editor-panel">
              <div className="editor-panel-head">
                <h2>{text.verifySegments}</h2>
                <p className="muted">{text.verifySegmentsHelp}</p>
              </div>

              <div className="row">
                <label>
                  {text.subtitleForOutput}
                  <select
                    value={project.subtitle_mode}
                    disabled={editorLocked}
                    onChange={(e) => void onSubtitleModeChange(e.target.value as SubtitleMode)}
                  >
                    <option value="none">{text.noSubtitles}</option>
                    <option value="source">{text.sourceSubtitles}</option>
                    <option value="target">{text.targetSubtitles}</option>
                  </select>
                </label>
              </div>

              <SubtitleEditor
                segments={segments}
                sourceLang={project.source_lang}
                targetLang={project.target_lang}
                disabled={editorLocked}
                onChange={onSegmentChange}
              />

              <div className="action-row editor-actions">
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={editorLocked}
                  onClick={() => void saveSegments().catch((err: Error) => setError(err.message))}
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
                  disabled={editorLocked || retranslating || segments.length === 0}
                  onClick={() => void onRetranslate()}
                >
                  {retranslating ? text.retranslating : text.retranslate}
                </button>
                <button
                  type="button"
                  className="btn-primary btn-dub"
                  disabled={editorLocked || segments.length === 0}
                  onClick={onCreateDub}
                >
                  {isDemoMode
                    ? canRegenerateDub
                      ? text.regenerateDubVoice
                      : text.createDubVoice
                    : project.status === "dubbing"
                      ? text.creatingDub
                      : text.createDubFile}
                </button>
                {hasDubVoice && (
                  <button
                    type="button"
                    className="btn-primary"
                    disabled={editorLocked}
                    onClick={onRenderDub}
                  >
                    {outputUrl ? text.rerenderFinalVideo : text.renderFinalVideo}
                  </button>
                )}
              </div>
              {message && <p className="form-msg ok">{message}</p>}
            </div>
          )}

          {!canEdit && project && (
            <div className="app-panel editor-panel">
              <p className="muted">
                {activeJob ? text.extractingSubtitles : text.noSubtitlesYet}
              </p>
            </div>
          )}

          {/* 3. Before / After */}
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
              />
            ) : (
              <p className="muted">{text.loadingSource}</p>
            )}
            <TranslationPreviewModal
              open={translationPreviewOpen}
              segments={segments}
              sourceLang={project.source_lang}
              targetLang={project.target_lang}
              onClose={() => setTranslationPreviewOpen(false)}
            />
            {!outputUrl && (
              <p className="muted" style={{ marginTop: "0.75rem" }}>
                {project.status === "completed" && hasDubVoice
                  ? text.afterPendingAfterEdit
                  : text.afterPending}
              </p>
            )}
            {outputUrl && (
              <div className="action-row">
                <button
                  type="button"
                  className="btn-primary"
                  onClick={() =>
                    void api.projects
                      .downloadFile(project.id)
                      .then((blob) =>
                        import("@/lib/demo-api").then(({ saveBlobDownload }) =>
                          saveBlobDownload(blob, `${project.title}-dubbed.mp4`),
                        ),
                      )
                      .catch((err: Error) => setError(err.message))
                  }
                >
                  {text.downloadFinal}
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}
