"use client";

import type { LangCode } from "@/lib/ui-types";

const LOCAL_PIPELINE_ORIGIN =
  process.env.NEXT_PUBLIC_LOCAL_PIPELINE_ORIGIN ?? "http://localhost:8002";

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
  return new URL(path, LOCAL_PIPELINE_ORIGIN).toString();
}

export async function checkLocalPipeline(): Promise<boolean> {
  try {
    const response = await fetch(`${LOCAL_PIPELINE_ORIGIN}/health`, {
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
            `${LOCAL_PIPELINE_ORIGIN}/v1/local/step12?source_lang=${sourceLang}&target_lang=${targetLang}&diarization_enabled=${diarizationEnabled}`,
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
    throw new Error(
      "실제 자막 추출 서버에 연결할 수 없습니다. api 폴더에서 " +
        "`uvicorn app.local_step12:app --reload --port 8002`를 실행해 주세요.",
    );
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
      `${LOCAL_PIPELINE_ORIGIN}/v1/local/step12/from-url?source_lang=${sourceLang}&target_lang=${targetLang}&diarization_enabled=${diarizationEnabled}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: mediaUrl }),
      },
    );
  } catch {
    throw new Error(
      "실제 자막 추출 서버에 연결할 수 없습니다. api 폴더에서 " +
        "`uvicorn app.local_step12:app --reload --port 8002`를 실행해 주세요.",
    );
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
    response = await fetch(`${LOCAL_PIPELINE_ORIGIN}/v1/local/retranslate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_lang: sourceLang,
        target_lang: targetLang,
        segments,
      }),
    });
  } catch {
    throw new Error(
      "실제 자막 추출 서버에 연결할 수 없습니다. api 폴더에서 " +
        "`uvicorn app.local_step12:app --reload --port 8002`를 실행해 주세요.",
    );
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
  segments: Array<{ idx: number; target_text: string }>,
  toneStyle: string,
): Promise<Array<{ idx: number; audio_url: string }>> {
  const response = await fetch(`${LOCAL_PIPELINE_ORIGIN}/v1/local/dub-voice`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      run_id: runId,
      segments,
      tone_style: toneStyle,
    }),
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(body?.detail ?? `더빙 음성 생성 실패 (${response.status})`);
  }
  const body = (await response.json()) as {
    segments: Array<{ idx: number; audio_url: string }>;
  };
  const bust = `t=${Date.now()}`;
  return body.segments.map((segment) => ({
    ...segment,
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
  const response = await fetch(`${LOCAL_PIPELINE_ORIGIN}/v1/local/render-dub`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      run_id: runId,
      segments,
      subtitle_mode: subtitleMode,
    }),
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(body?.detail ?? `최종 더빙 영상 생성 실패 (${response.status})`);
  }
  const body = (await response.json()) as {
    source_url: string;
    output_url: string;
    warnings: string[];
  };
  return {
    ...body,
    source_url: absoluteAssetUrl(body.source_url),
    output_url: absoluteAssetUrl(body.output_url),
  };
}

export async function deleteLocalRun(runId: string): Promise<void> {
  const response = await fetch(
    `${LOCAL_PIPELINE_ORIGIN}/v1/local/runs/${encodeURIComponent(runId)}`,
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
  const response = await fetch(`${LOCAL_PIPELINE_ORIGIN}/v1/local/runs/gc`, {
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
