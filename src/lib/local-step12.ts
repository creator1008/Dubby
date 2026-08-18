"use client";

import type { LangCode } from "@/lib/ui-types";

const PIPELINE_ORIGIN_STORAGE_KEY = "dubby.localPipelineOrigin";
const BUILTIN_PIPELINE_ORIGIN = (
  process.env.NEXT_PUBLIC_LOCAL_PIPELINE_ORIGIN ?? "http://localhost:8002"
).replace(/\/$/, "");

function normalizePipelineOrigin(value: string | null | undefined): string | null {
  const raw = (value || "").trim().replace(/\/$/, "");
  if (!raw) return null;
  try {
    const url = new URL(raw);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    return url.origin;
  } catch {
    return null;
  }
}

/** Resolve API origin: ?pipeline=… / localStorage override, else build-time env. */
export function getLocalPipelineOrigin(): string {
  if (typeof window !== "undefined") {
    try {
      const params = new URLSearchParams(window.location.search);
      const fromQuery = normalizePipelineOrigin(params.get("pipeline"));
      if (fromQuery) {
        window.localStorage.setItem(PIPELINE_ORIGIN_STORAGE_KEY, fromQuery);
        return fromQuery;
      }
      const fromStorage = normalizePipelineOrigin(
        window.localStorage.getItem(PIPELINE_ORIGIN_STORAGE_KEY),
      );
      if (fromStorage) return fromStorage;
    } catch {
      /* ignore storage / URL errors */
    }
  }
  return BUILTIN_PIPELINE_ORIGIN;
}

function pipelineUnreachableMessage(): string {
  const origin = getLocalPipelineOrigin();
  const isLocalOrigin = /^(https?:\/\/)?(localhost|127\.0\.0\.1)(:\d+)?$/i.test(
    origin.replace(/\/$/, ""),
  );
  if (isLocalOrigin) {
    return (
      "자막 추출 서버(localhost:8002)에 연결할 수 없습니다. " +
      "PC에서는 `api` 폴더에서 `uvicorn app.local_step12:app --reload --port 8002`를 실행하세요. " +
      "휴대폰/GitHub Pages에서는 공개 HTTPS 주소(예: Cloudflare Tunnel)를 " +
      "NEXT_PUBLIC_LOCAL_PIPELINE_ORIGIN에 넣고 다시 배포해야 합니다."
    );
  }
  return (
    `자막 추출 서버(${origin})에 연결할 수 없습니다. ` +
    "서버가 실행 중인지, CORS에 GitHub Pages origin이 허용되는지 확인하세요. " +
    "터널 URL이 바뀌었다면 ?pipeline=https://….trycloudflare.com 으로 열어 저장할 수 있습니다."
  );
}

function networkErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof TypeError) {
    return (
      "네트워크 연결이 끊겼습니다(Failed to fetch). " +
      "PC에서 API·터널이 켜져 있는지 확인하고, 긴 작업은 잠시 후 다시 시도해 주세요."
    );
  }
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}

type LocalJobResponse<T> = {
  job_id: string;
  status: "running" | "done" | "error" | string;
  kind?: string;
  error?: string | null;
  result?: T;
};

async function waitForLocalJob<T>(
  jobId: string,
  label: string,
  timeoutMs = 15 * 60 * 1000,
): Promise<T> {
  const started = Date.now();
  let delayMs = 1500;
  while (Date.now() - started < timeoutMs) {
    let response: Response;
    try {
      response = await fetch(
        `${getLocalPipelineOrigin()}/v1/local/jobs/${encodeURIComponent(jobId)}`,
        { cache: "no-store" },
      );
    } catch (err) {
      throw new Error(networkErrorMessage(err, `${label} 상태 확인 실패`));
    }
    if (!response.ok) {
      const body = (await response.json().catch(() => null)) as {
        detail?: string;
      } | null;
      throw new Error(body?.detail ?? `${label} 상태 확인 실패 (${response.status})`);
    }
    const job = (await response.json()) as LocalJobResponse<T>;
    if (job.status === "done") {
      if (job.result === undefined || job.result === null) {
        throw new Error(`${label} 결과가 비어 있습니다.`);
      }
      return job.result;
    }
    if (job.status === "error") {
      throw new Error(job.error || `${label} 실패`);
    }
    await new Promise((resolve) => window.setTimeout(resolve, delayMs));
    delayMs = Math.min(5000, delayMs + 500);
  }
  throw new Error(`${label} 시간이 초과되었습니다. PC 서버 상태를 확인한 뒤 다시 시도해 주세요.`);
}
export type LocalSpeechPair = {
  idx: number;
  start_ms: number;
  end_ms: number;
  text: string;
  target_text: string;
  speaker_id: string | null;
  audio_path: string;
  audio_url: string;
};

export type LocalStep12Result = {
  run_id: string;
  language: LangCode;
  source_url: string;
  audio_path: string;
  audio_url: string;
  asr_audio_path: string;
  asr_audio_url: string;
  segments: LocalSpeechPair[];
};

function absoluteAssetUrl(path: string) {
  return new URL(path, getLocalPipelineOrigin()).toString();
}

export async function checkLocalPipeline(): Promise<boolean> {
  try {
    const response = await fetch(`${getLocalPipelineOrigin()}/health`, {
      signal: AbortSignal.timeout(1500),
    });
    return response.ok;
  } catch {
    return false;
  }
}

export async function extractLocalStep12(
  file: File,
  sourceLang: LangCode,
  targetLang: LangCode,
  diarizationEnabled = false,
): Promise<LocalStep12Result> {
  let response: Response;
  try {
    response = await fetch(
            `${getLocalPipelineOrigin()}/v1/local/step12?source_lang=${sourceLang}&target_lang=${targetLang}&diarization_enabled=${diarizationEnabled}`,
      {
        method: "POST",
        headers: {
          "Content-Type": file.type || "application/octet-stream",
          "X-Filename": file.name,
        },
        body: file,
      },
    );
  } catch {
    throw new Error(pipelineUnreachableMessage());
  }

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string | { message?: string };
    } | null;
    const detail =
      typeof body?.detail === "string"
        ? body.detail
        : body?.detail?.message;
    throw new Error(detail ?? `실제 자막 추출 실패 (${response.status})`);
  }

  const result = (await response.json()) as LocalStep12Result;
  return {
    ...result,
    source_url: absoluteAssetUrl(result.source_url),
    audio_url: absoluteAssetUrl(result.audio_url),
    asr_audio_url: absoluteAssetUrl(result.asr_audio_url),
    segments: result.segments.map((segment) => ({
      ...segment,
      audio_url: absoluteAssetUrl(segment.audio_url),
    })),
  };
}

export async function extractLocalStep12FromUrl(
  mediaUrl: string,
  sourceLang: LangCode,
  targetLang: LangCode,
  diarizationEnabled = false,
): Promise<LocalStep12Result> {
  let response: Response;
  try {
    response = await fetch(
      `${getLocalPipelineOrigin()}/v1/local/step12/from-url?source_lang=${sourceLang}&target_lang=${targetLang}&diarization_enabled=${diarizationEnabled}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: mediaUrl }),
      },
    );
  } catch {
    throw new Error(pipelineUnreachableMessage());
  }

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string | { message?: string };
    } | null;
    const detail =
      typeof body?.detail === "string"
        ? body.detail
        : body?.detail?.message;
    throw new Error(detail ?? `영상 링크 처리 실패 (${response.status})`);
  }

  const result = (await response.json()) as LocalStep12Result;
  return {
    ...result,
    source_url: absoluteAssetUrl(result.source_url),
    audio_url: absoluteAssetUrl(result.audio_url),
    asr_audio_url: absoluteAssetUrl(result.asr_audio_url),
    segments: result.segments.map((segment) => ({
      ...segment,
      audio_url: absoluteAssetUrl(segment.audio_url),
    })),
  };
}

export async function retranslateLocalSegments(
  sourceLang: LangCode,
  targetLang: LangCode,
  segments: Array<{
    idx: number;
    start_ms: number;
    end_ms: number;
    source_text: string;
  }>,
): Promise<Array<{ idx: number; target_text: string }>> {
  let response: Response;
  try {
    response = await fetch(`${getLocalPipelineOrigin()}/v1/local/retranslate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_lang: sourceLang,
        target_lang: targetLang,
        segments,
      }),
    });
  } catch {
    throw new Error(pipelineUnreachableMessage());
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(body?.detail ?? `다시 번역 실패 (${response.status})`);
  }
  const body = (await response.json()) as {
    segments: Array<{ idx: number; target_text: string }>;
  };
  return body.segments;
}

export async function generateLocalDubVoice(
  runId: string,
  segments: Array<{ idx: number; target_text: string; speak_speed?: number }>,
  toneStyle: string,
  voiceIds: string[] = [],
): Promise<Array<{ idx: number; audio_url: string; speak_speed?: number }>> {
  let response: Response;
  try {
    response = await fetch(`${getLocalPipelineOrigin()}/v1/local/dub-voice`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        run_id: runId,
        segments,
        tone_style: toneStyle,
        voice_ids: voiceIds,
      }),
    });
  } catch (err) {
    throw new Error(networkErrorMessage(err, "더빙 음성 생성 요청 실패"));
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(body?.detail ?? `더빙 음성 생성 실패 (${response.status})`);
  }
  const started = (await response.json()) as LocalJobResponse<{
    segments: Array<{ idx: number; audio_url: string; speak_speed?: number }>;
  }> & {
    segments?: Array<{ idx: number; audio_url: string; speak_speed?: number }>;
  };
  const body =
    started.job_id && started.status === "running"
      ? await waitForLocalJob<{
          segments: Array<{ idx: number; audio_url: string; speak_speed?: number }>;
        }>(started.job_id, "더빙 음성 생성")
      : started;
  if (!body.segments) {
    throw new Error("더빙 음성 결과가 비어 있습니다.");
  }
  const bust = `t=${Date.now()}`;
  return body.segments.map((segment) => ({
    ...segment,
    speak_speed:
      typeof segment.speak_speed === "number" && Number.isFinite(segment.speak_speed)
        ? segment.speak_speed
        : 1,
    audio_url: `${absoluteAssetUrl(segment.audio_url)}?${bust}`,
  }));
}

export async function renderLocalDubVideo(
  runId: string,
  segments: Array<{
    idx: number;
    start_ms: number;
    end_ms: number;
    source_text: string;
    target_text: string;
  }>,
  subtitleMode: "none" | "source" | "target",
): Promise<{
  source_url: string;
  output_url: string;
  warnings: string[];
}> {
  let response: Response;
  try {
    response = await fetch(`${getLocalPipelineOrigin()}/v1/local/render-dub`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        run_id: runId,
        segments,
        subtitle_mode: subtitleMode,
      }),
    });
  } catch (err) {
    throw new Error(networkErrorMessage(err, "최종 더빙 영상 생성 요청 실패"));
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(body?.detail ?? `최종 더빙 영상 생성 실패 (${response.status})`);
  }
  const started = (await response.json()) as LocalJobResponse<{
    source_url: string;
    output_url: string;
    warnings: string[];
  }> & {
    source_url?: string;
    output_url?: string;
    warnings?: string[];
  };
  const body =
    started.job_id && started.status === "running"
      ? await waitForLocalJob<{
          source_url: string;
          output_url: string;
          warnings: string[];
        }>(started.job_id, "최종 더빙 영상 생성")
      : started;
  if (!body.source_url || !body.output_url) {
    throw new Error("최종 더빙 영상 결과가 비어 있습니다.");
  }
  return {
    source_url: absoluteAssetUrl(body.source_url),
    output_url: absoluteAssetUrl(body.output_url),
    warnings: body.warnings ?? [],
  };
}

export async function deleteLocalRun(runId: string): Promise<void> {
  const response = await fetch(
    `${getLocalPipelineOrigin()}/v1/local/runs/${encodeURIComponent(runId)}`,
    { method: "DELETE" },
  );
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(body?.detail ?? `로컬 실행 삭제 실패 (${response.status})`);
  }
}

/** Delete every local/R2 run that is not listed in ``keepRunIds``. */
export async function gcOrphanLocalRuns(keepRunIds: string[]): Promise<{
  deleted_count: number;
}> {
  const response = await fetch(`${getLocalPipelineOrigin()}/v1/local/runs/gc`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ keep_run_ids: keepRunIds }),
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(body?.detail ?? `고아 미디어 정리 실패 (${response.status})`);
  }
  return (await response.json()) as { deleted_count: number };
}
