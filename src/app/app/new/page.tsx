"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { FileUploader } from "@/components/app/FileUploader";
import { JobProgress } from "@/components/app/JobProgress";
import { SubtitleEditor } from "@/components/app/SubtitleEditor";
import { BeforeAfterPlayer } from "@/components/landing/BeforeAfterPlayer";
import { api, isDemoMode, uploadSourceFile } from "@/lib/api";
import { useVoiceConsent } from "@/lib/consent";
import { demoApi } from "@/lib/demo-api";
import {
  extractLocalStep12,
  extractLocalStep12FromUrl,
  generateLocalDubVoice,
  renderLocalDubVideo,
} from "@/lib/local-step12";
import { useAppDictionary } from "@/lib/i18n/locale-context";
import type {
  Job,
  LangCode,
  Project,
  Segment,
  SubtitleMode,
  ToneStyle,
} from "@/lib/ui-types";

export default function NewDubPage() {
  const text = useAppDictionary();
  const [title, setTitle] = useState("");
  const [sourceLang, setSourceLang] = useState<LangCode>("ko");
  const [targetLang, setTargetLang] = useState<LangCode>("en");
  const [subtitleMode, setSubtitleMode] = useState<SubtitleMode>("target");
  const [toneStyle, setToneStyle] = useState<ToneStyle>("neutral");
  const [diarizationEnabled, setDiarizationEnabled] = useState(true);
  const [sourceMode, setSourceMode] = useState<"upload" | "url">("upload");
  const [file, setFile] = useState<File | null>(null);
  const [videoUrl, setVideoUrl] = useState("");
  const [uploadPct, setUploadPct] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [localStage, setLocalStage] = useState<string | null>(null);
  const [localRunId, setLocalRunId] = useState<string | null>(null);
  const [extractedAudioUrl, setExtractedAudioUrl] = useState<string | null>(null);
  const [dubbedAudioByIdx, setDubbedAudioByIdx] = useState<Record<number, string>>({});

  const [project, setProject] = useState<Project | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [sourceUrl, setSourceUrl] = useState<string | null>(null);
  const [voiceRemovedUrl, setVoiceRemovedUrl] = useState<string | null>(null);
  const [outputUrl, setOutputUrl] = useState<string | null>(null);

  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const voiceConsent = useVoiceConsent();

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
      setSegments(await api.segments.list(project.id));
    }
    if (nextProject.status === "completed" && !outputUrl) {
      const { url } = await api.projects.download(project.id);
      setOutputUrl(url);
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
  const onExtract = async (e: FormEvent) => {
    e.preventDefault();
    const remoteUrl = videoUrl.trim();
    if (sourceMode === "upload" && !file) {
      setError(text.selectVideoFile);
      return;
    }
    if (sourceMode === "url" && !remoteUrl) {
      setError(text.enterVideoUrl);
      return;
    }
    if (!voiceConsent.accepted) {
      setError("음성 처리 권한과 동의를 확인해 주세요.");
      return;
    }
    if (sourceLang === targetLang) {
      setError("원어와 더빙 언어가 같습니다.");
      return;
    }
    setUploading(true);
    setUploadPct(0);
    setError(null);
    try {
      let sourceTitle = file?.name ?? text.linkVideo;
      if (sourceMode === "url") {
        try {
          const parsed = new URL(remoteUrl);
          sourceTitle = decodeURIComponent(
            parsed.pathname.split("/").filter(Boolean).at(-1) || parsed.hostname,
          );
        } catch {
          setError(text.invalidVideoUrl);
          return;
        }
      }
      const created = await api.projects.create({
        title: title.trim() || sourceTitle,
        source_lang: sourceLang,
        target_lang: targetLang,
        subtitle_mode: subtitleMode,
        tone_style: toneStyle,
        diarization_enabled: diarizationEnabled,
      });
      if (isDemoMode) {
        setLocalStage("2/2 실제 음성 인식 및 단어 타임스탬프 추출 중");
        const result = sourceMode === "upload"
          ? await (async () => {
              await uploadSourceFile(created.id, file!, setUploadPct);
              return extractLocalStep12(
                file!,
                sourceLang,
                targetLang,
                diarizationEnabled,
              );
            })()
          : await extractLocalStep12FromUrl(
              remoteUrl,
              sourceLang,
              targetLang,
              diarizationEnabled,
            ).then((result) => {
              setUploadPct(100);
              return result;
            });
        const extracted = await demoApi.applyStep12(created.id, result);
        setSegments(extracted);
        setLocalRunId(result.run_id);
        setExtractedAudioUrl(result.audio_url);
        setLocalStage(null);
      } else {
        if (sourceMode === "upload") {
          await uploadSourceFile(created.id, file!, setUploadPct);
        } else {
          await api.projects.importUrl(created.id, remoteUrl);
          setUploadPct(100);
        }
        await api.jobs.create(created.id, "transcribe");
      }
      const [nextProject, nextJobs] = await Promise.all([
        api.projects.get(created.id),
        api.jobs.list(created.id),
      ]);
      setProject(nextProject);
      setJobs(nextJobs);
      void api.projects
        .sourceUrl(created.id)
        .then(({ url }) => setSourceUrl(url))
        .catch(() => undefined);
    } catch (err) {
      setLocalStage(null);
      setError(err instanceof Error ? err.message : "업로드하지 못했습니다.");
    } finally {
      setUploading(false);
    }
  };

  // Step 2 — 자막 에디터
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

  const saveSegments = async () => {
    if (!project) return;
    setBusy(true);
    try {
      setSegments(
        await api.segments.update(
          project.id,
          segments.map(({ id, source_text, target_text }) => ({
            id,
            source_text,
            target_text,
          })),
        ),
      );
      setMessage("자막을 저장했습니다.");
    } finally {
      setBusy(false);
    }
  };

  // 자막언어 선택 — 더빙 출력에 들어갈 자막
  const onSubtitleModeChange = async (mode: SubtitleMode) => {
    setSubtitleMode(mode);
    if (!project) return;
    try {
      setProject(await api.projects.update(project.id, { subtitle_mode: mode }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "자막 설정을 저장하지 못했습니다.");
    }
  };

  // Step 3 — 더빙 파일 생성
  const onCreateDub = async () => {
    if (!project) return;
    setError(null);
    setOutputUrl(null);
    try {
      await saveSegments();
      if (isDemoMode) {
        if (!localRunId) {
          throw new Error("로컬 추출 작업 ID가 없습니다. 파일을 다시 추출해 주세요.");
        }
        const speakable = segments
          .filter((segment) => segment.target_text.trim())
          .map((segment) => ({
            idx: segment.idx,
            target_text: segment.target_text.trim(),
          }));
        if (!speakable.length) {
          throw new Error("더빙할 번역 텍스트가 없습니다.");
        }
        setLocalStage(
          "Demucs 보이스 분리 → 깨끗한 보이스 샘플로 클론 → ElevenLabs 더빙 음성 생성 중",
        );
        const outputs = await generateLocalDubVoice(
          localRunId,
          speakable,
          toneStyle,
        );
        setSegments(await demoApi.applyDubVoice(project.id, outputs));
        setDubbedAudioByIdx(
          Object.fromEntries(outputs.map((output) => [output.idx, output.audio_url])),
        );
        setLocalStage(null);
        setMessage("더빙 음성을 생성했습니다. 각 구간에서 결과를 들어보세요.");
        return;
      }
      await api.jobs.create(project.id, "dub");
      window.dispatchEvent(new Event("credits-changed"));
      await refresh();
    } catch (err) {
      setLocalStage(null);
      setError(err instanceof Error ? err.message : "더빙을 시작하지 못했습니다.");
    }
  };

  const onRenderDub = async () => {
    if (!project || !localRunId) return;
    setError(null);
    setLocalStage(
      "언어 인식 구간만 보이스 제거 → 0.2초 보정 → 비언어·배경음 보존 → 영상 합성 중",
    );
    try {
      const result = await renderLocalDubVideo(
        localRunId,
        segments.map(
          ({ idx, start_ms, end_ms, source_text, target_text }) => ({
            idx,
            start_ms,
            end_ms,
            source_text,
            target_text,
          }),
        ),
        project.subtitle_mode,
      );
      setVoiceRemovedUrl(result.voice_removed_url);
      setOutputUrl(result.output_url);
      setProject(await demoApi.applyRender(project.id, result));
      setMessage(
        result.warnings.length
          ? `최종 영상을 생성했습니다. ${result.warnings.join(" ")}`
          : "보이스 제거 영상에 목표 언어 음성을 합성했습니다.",
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "최종 영상을 생성하지 못했습니다.");
    } finally {
      setLocalStage(null);
    }
  };

  const editorLocked =
    busy || uploading || Boolean(activeJob) || Boolean(localStage);
  const canEdit = segments.length > 0;

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
                <option value="ko">한국어</option>
                <option value="en">English</option>
                <option value="vi">Tiếng Việt</option>
              </select>
            </label>
            <label>
              {text.targetLanguage}
              <select
                value={targetLang}
                disabled={uploading}
                onChange={(e) => setTargetLang(e.target.value as LangCode)}
              >
                <option value="en">English</option>
                <option value="ko">한국어</option>
                <option value="vi">Tiếng Việt</option>
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
                onChange={(e) => setDiarizationEnabled(e.target.value === "multi")}
              >
                <option value="multi">{text.multiSpeaker}</option>
                <option value="single">{text.singleSpeaker}</option>
              </select>
            </label>
          </div>

          <div className="source-mode-tabs" role="group" aria-label={text.videoSourceMethod}>
            <button
              type="button"
              className={sourceMode === "upload" ? "btn-primary" : "btn-ghost"}
              disabled={uploading}
              onClick={() => setSourceMode("upload")}
            >
              {text.uploadVideo}
            </button>
            <button
              type="button"
              className={sourceMode === "url" ? "btn-primary" : "btn-ghost"}
              disabled={uploading}
              onClick={() => setSourceMode("url")}
            >
              {text.videoLink}
            </button>
          </div>

          {sourceMode === "upload" ? (
            <FileUploader file={file} onFile={setFile} disabled={uploading} />
          ) : (
            <label className="video-url-field">
              {text.videoLink}
              <input
                type="url"
                value={videoUrl}
                disabled={uploading}
                placeholder="https://cdn.example.com/video.mp4"
                onChange={(event) => setVideoUrl(event.target.value)}
              />
              <span className="muted">{text.directVideoUrlHint}</span>
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
                <strong>
                  {sourceMode === "upload" ? text.fileUpload : text.linkImport}
                </strong>
                <span>{uploadPct}%</span>
              </div>
              <div className="progress-bar" role="progressbar" aria-valuenow={uploadPct}>
                <span style={{ width: `${uploadPct}%` }} />
              </div>
            </div>
          )}

          <button
            className="btn-primary"
            type="submit"
            disabled={
              uploading
              || (sourceMode === "upload" ? !file : !videoUrl.trim())
              || !voiceConsent.accepted
            }
          >
            {uploading
              ? text.uploading
              : sourceMode === "upload"
                ? text.uploadAndExtract
                : text.importAndExtract}
          </button>
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
          {/* 2. timestamp + speech clip + text verification */}
          <div className="app-panel editor-panel">
            <div className="editor-panel-head">
              <h2>{text.verifySegments}</h2>
              <p className="muted">
                {text.verifySegmentsHelp}
              </p>
            </div>

            {extractedAudioUrl && (
              <div className="step-audio-result">
                <strong>{text.extractedAudio}</strong>
                <audio controls preload="metadata" src={extractedAudioUrl} />
              </div>
            )}

            {!canEdit && (
              <p className="muted">
                {activeJob ? text.extractingSubtitles : text.noSubtitlesYet}
              </p>
            )}

            {canEdit && (
              <>
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
                  segments={segments.map((segment) => ({
                    ...segment,
                    dubbed_audio_url: dubbedAudioByIdx[segment.idx],
                  }))}
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
                  {isDemoMode && Object.keys(dubbedAudioByIdx).length > 0 && (
                    <button
                      type="button"
                      className="btn-primary"
                      disabled={editorLocked}
                      onClick={onRenderDub}
                    >
                      {text.renderFinalVideo}
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn-primary btn-dub"
                    disabled={editorLocked || segments.length === 0}
                    onClick={onCreateDub}
                  >
                    {isDemoMode
                      ? text.createDubVoice
                      : project.status === "dubbing"
                        ? text.creatingDub
                        : text.createDubFile}
                  </button>
                </div>
                {message && <p className="form-msg ok">{message}</p>}
              </>
            )}
          </div>

          {/* 3. Before / After */}
          <div className="app-panel">
            <h2 className="panel-inline-title">{text.beforeAfter}</h2>
            {sourceUrl ? (
              <BeforeAfterPlayer
                beforeSrc={voiceRemovedUrl ?? sourceUrl}
                afterSrc={outputUrl ?? ""}
                beforeLabel={
                  voiceRemovedUrl
                    ? text.beforeVoiceRemoved
                    : text.beforeOriginal
                }
                afterLabel={text.afterDubbed}
                segments={segments}
                subtitleMode={project.subtitle_mode}
              />
            ) : (
              <p className="muted">{text.loadingSource}</p>
            )}
            {!outputUrl && (
              <p className="muted" style={{ marginTop: "0.75rem" }}>
                {text.afterPending}
              </p>
            )}
            {outputUrl && (
              <div className="action-row">
                <a
                  href={`${outputUrl}${outputUrl.includes("?") ? "&" : "?"}download=${encodeURIComponent(`${project.title}-dubbed.mp4`)}`}
                  className="btn-primary"
                  download={`${project.title}-dubbed.mp4`}
                >
                  {text.downloadFinal}
                </a>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}
