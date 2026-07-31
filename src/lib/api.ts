import { withBasePath } from "@/lib/base-path";
import { demoApi, isDemoMode } from "@/lib/demo-api";
import { getSupabase } from "@/lib/supabase";
import type {
  AccessLog,
  AdminUser,
  AdminUserUsage,
  Credits,
  Job,
  Project,
  Segment,
} from "@/lib/ui-types";

const API_ORIGIN_STORAGE_KEY = "dubby.apiOrigin";
const BUILTIN_API_ORIGIN = (process.env.NEXT_PUBLIC_API_ORIGIN ?? "").replace(
  /\/$/,
  "",
);
const HEALTHY_ORIGIN_TTL_MS = 45_000;

let healthyOriginCache: { origin: string; checkedAt: number } | null = null;

function markHealthy(origin: string): string {
  healthyOriginCache = { origin, checkedAt: Date.now() };
  return origin;
}

function normalizeApiOrigin(value: string | null | undefined): string | null {
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

function rememberApiOrigin(origin: string): string {
  try {
    window.localStorage.setItem(API_ORIGIN_STORAGE_KEY, origin);
  } catch {
    /* ignore */
  }
  return origin;
}

/** Resolve API origin: ?api=… / localStorage override, else build-time env. */
export function getApiOrigin(): string {
  if (typeof window !== "undefined") {
    try {
      const params = new URLSearchParams(window.location.search);
      const fromQuery = normalizeApiOrigin(params.get("api"));
      if (fromQuery) return rememberApiOrigin(fromQuery);
      const fromStorage = normalizeApiOrigin(
        window.localStorage.getItem(API_ORIGIN_STORAGE_KEY),
      );
      if (fromStorage) return fromStorage;
    } catch {
      /* ignore storage / URL errors */
    }
  }
  return BUILTIN_API_ORIGIN;
}

/** Pull the latest tunnel URL published to GitHub Pages (no rebuild required for clients that already cache this file fetch). */
export async function fetchPublishedApiOrigin(): Promise<string | null> {
  if (typeof window === "undefined") return null;
  try {
    const response = await fetch(
      `${withBasePath("/api-origin.json")}?t=${Date.now()}`,
      { cache: "no-store" },
    );
    if (!response.ok) return null;
    const body = (await response.json()) as { api_origin?: string };
    const origin = normalizeApiOrigin(body.api_origin);
    if (!origin) return null;
    return rememberApiOrigin(origin);
  } catch {
    return null;
  }
}

async function healthOk(origin: string, timeoutMs: number): Promise<boolean> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${origin}/healthz`, {
      method: "GET",
      cache: "no-store",
      signal: controller.signal,
    });
    return response.ok;
  } catch {
    return false;
  } finally {
    window.clearTimeout(timer);
  }
}

/**
 * Prefer a live API origin. If the cached/build-time tunnel is dead, refresh
 * from public/api-origin.json (kept current by scripts/keep-api-tunnel.sh).
 */
export async function ensureApiOrigin(timeoutMs = 8000): Promise<string> {
  const cached = healthyOriginCache;
  if (
    cached &&
    Date.now() - cached.checkedAt < HEALTHY_ORIGIN_TTL_MS &&
    cached.origin
  ) {
    return cached.origin;
  }

  const current = getApiOrigin();
  if (current && (await healthOk(current, timeoutMs))) {
    return markHealthy(current);
  }

  const published = await fetchPublishedApiOrigin();
  if (
    published &&
    published !== current &&
    (await healthOk(published, timeoutMs))
  ) {
    return markHealthy(published);
  }

  // Also try build-time origin if localStorage pointed at a stale tunnel.
  if (
    BUILTIN_API_ORIGIN &&
    BUILTIN_API_ORIGIN !== current &&
    BUILTIN_API_ORIGIN !== published &&
    (await healthOk(BUILTIN_API_ORIGIN, timeoutMs))
  ) {
    return markHealthy(rememberApiOrigin(BUILTIN_API_ORIGIN));
  }

  healthyOriginCache = null;
  throw new ApiError(apiUnreachableMessage(), 0);
}

function apiUnreachableMessage(): string {
  const origin = getApiOrigin() || "(미설정)";
  return (
    `API 서버(${origin})에 연결할 수 없습니다(Failed to fetch). ` +
    "PC에서 `bash scripts/keep-api-tunnel.sh` 로 터널을 다시 열어 주세요. " +
    "이미 새 터널이 배포됐다면 화면을 새로고침하면 자동으로 주소를 갱신합니다."
  );
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

/** Lightweight reachability check used before long extract/dub flows. */
export async function pingApi(timeoutMs = 8000): Promise<void> {
  await ensureApiOrigin(timeoutMs);
}

async function requestBlob(path: string): Promise<Blob> {
  const supabase = getSupabase();
  const { data } = supabase
    ? await supabase.auth.getSession()
    : { data: { session: null } };
  if (!data.session) throw new ApiError("로그인이 필요합니다.", 401);
  const apiOrigin = await ensureApiOrigin();

  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 10 * 60 * 1000);
  try {
    const response = await fetch(`${apiOrigin}${path}`, {
      headers: {
        Authorization: `Bearer ${data.session.access_token}`,
      },
      signal: controller.signal,
    });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      const detail =
        typeof body?.detail === "string"
          ? body.detail
          : `요청 실패 (${response.status})`;
      throw new ApiError(detail, response.status);
    }
    return await response.blob();
  } catch (err) {
    if (err instanceof ApiError) throw err;
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError("다운로드 시간이 초과되었습니다. 다시 시도해 주세요.", 408);
    }
    if (err instanceof TypeError) {
      throw new ApiError(apiUnreachableMessage(), 0);
    }
    throw new ApiError(
      err instanceof Error ? err.message : "다운로드에 실패했습니다.",
      0,
    );
  } finally {
    window.clearTimeout(timer);
  }
}

async function sleep(ms: number) {
  await new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const supabase = getSupabase();
  const { data } = supabase
    ? await supabase.auth.getSession()
    : { data: { session: null } };
  if (!data.session) throw new ApiError("로그인이 필요합니다.", 401);
  let apiOrigin = await ensureApiOrigin();

  const method = (init?.method || "GET").toUpperCase();
  const retries = method === "GET" || method === "HEAD" ? 3 : 2;
  let lastError: unknown;
  for (let attempt = 1; attempt <= retries; attempt += 1) {
    let response: Response;
    try {
      response = await fetch(`${apiOrigin}${path}`, {
        ...init,
        headers: {
          Authorization: `Bearer ${data.session.access_token}`,
          ...(init?.body ? { "Content-Type": "application/json" } : {}),
          ...init?.headers,
        },
      });
    } catch (err) {
      lastError = err;
      if (err instanceof TypeError && attempt < retries) {
        // Tunnel URL may have rotated — refresh once mid-retry.
        healthyOriginCache = null;
        try {
          apiOrigin = await ensureApiOrigin();
        } catch {
          /* keep previous origin */
        }
        await sleep(700 * attempt);
        continue;
      }
      if (err instanceof TypeError) {
        throw new ApiError(apiUnreachableMessage(), 0);
      }
      throw err;
    }
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new ApiError(body?.detail ?? `요청 실패 (${response.status})`, response.status);
    }
    if (response.status === 204) return undefined as T;
    return response.json() as Promise<T>;
  }
  if (lastError instanceof TypeError) {
    throw new ApiError(apiUnreachableMessage(), 0);
  }
  throw lastError instanceof Error
    ? lastError
    : new ApiError(apiUnreachableMessage(), 0);
}

const realApi = {
  projects: {
    list: () => request<Project[]>("/v1/projects"),
    get: (id: string) => request<Project>(`/v1/projects/${id}`),
    create: (body: Pick<Project, "title" | "source_lang" | "target_lang" | "subtitle_mode" | "tone_style" | "diarization_enabled">) =>
      request<Project>("/v1/projects", { method: "POST", body: JSON.stringify(body) }),
    update: (
      id: string,
      body: Partial<Pick<Project, "tone_style" | "diarization_enabled" | "subtitle_mode">>,
    ) => request<Project>(`/v1/projects/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
    remove: (id: string) => request<void>(`/v1/projects/${id}`, { method: "DELETE" }),
    download: (id: string) =>
      request<{ url: string; expires_in: number }>(`/v1/projects/${id}/output-url`),
    downloadFile: (id: string) => requestBlob(`/v1/projects/${id}/output`),
    outputUrl: (id: string) =>
      request<{ url: string; expires_in: number }>(`/v1/projects/${id}/output-url`),
    sourceUrl: (id: string) =>
      request<{ url: string; expires_in: number }>(`/v1/projects/${id}/source-url`),
    voiceRemovedUrl: (id: string) =>
      request<{ url: string; expires_in: number }>(
        `/v1/projects/${id}/voice-removed-url`,
      ),
    sourceFromUrl: (id: string, url: string) =>
      request<Project>(`/v1/projects/${id}/source-from-url`, {
        method: "POST",
        body: JSON.stringify({ url }),
      }),
  },
  segments: {
    list: (projectId: string) =>
      request<Segment[]>(`/v1/projects/${projectId}/segments`),
    update: (
      projectId: string,
      segments: Array<Pick<Segment, "id" | "target_text"> & { source_text?: string }>,
    ) =>
      request<Segment[]>(`/v1/projects/${projectId}/segments`, {
        method: "PUT",
        body: JSON.stringify({ segments }),
      }),
    retranslate: (
      projectId: string,
      segments: Array<Pick<Segment, "id" | "source_text">>,
    ) =>
      request<Segment[]>(`/v1/projects/${projectId}/segments/retranslate`, {
        method: "POST",
        body: JSON.stringify({ segments }),
      }),
  },
  jobs: {
    list: (projectId: string) => request<Job[]>(`/v1/projects/${projectId}/jobs`),
    create: (projectId: string, kind: "transcribe" | "dub" | "lipsync") =>
      request<Job>(`/v1/projects/${projectId}/jobs`, {
        method: "POST",
        body: JSON.stringify({ kind }),
      }),
    get: (jobId: string) => request<Job>(`/v1/jobs/${jobId}`),
    cancel: (jobId: string) =>
      request<Job>(`/v1/jobs/${jobId}/cancel`, { method: "POST" }),
  },
  credits: () => request<Credits>("/v1/credits"),
  checkout: (kind: "subscription" | "credits") =>
    request<{ url: string }>("/v1/billing/checkout", {
      method: "POST",
      body: JSON.stringify({ kind }),
    }),
  admin: {
    users: (query = "") =>
      request<AdminUser[]>(`/v1/admin/users?query=${encodeURIComponent(query)}`),
    userUsage: (userId: string) =>
      request<AdminUserUsage>(`/v1/admin/users/${userId}`),
    accessLogs: () => request<AccessLog[]>("/v1/admin/access-logs"),
    adjustCredits: (userId: string, deltaMinutes: number, note: string) =>
      request<{ balance_minutes: number }>(`/v1/admin/users/${userId}/credits`, {
        method: "POST",
        body: JSON.stringify({ delta_minutes: deltaMinutes, note }),
      }),
    setUserActive: (userId: string, isActive: boolean) =>
      request<{ profile: AdminUserUsage["profile"] }>(
        `/v1/admin/users/${userId}/status`,
        {
          method: "PATCH",
          body: JSON.stringify({ is_active: isActive }),
        },
      ),
  },
  uploads: {
    create: (body: {
      project_id: string;
      filename: string;
      content_type: string;
      size_bytes: number;
    }) => request<{
      upload_id: string;
      key: string;
      part_size_bytes: number;
      part_count: number;
    }>("/v1/uploads/multipart", { method: "POST", body: JSON.stringify(body) }),
    signPart: (uploadId: string, key: string, partNumber: number) =>
      request<{ url: string }>(`/v1/uploads/multipart/${uploadId}/parts`, {
        method: "POST",
        body: JSON.stringify({ key, part_number: partNumber }),
      }),
    complete: (
      uploadId: string,
      key: string,
      parts: Array<{ part_number: number; etag: string }>,
    ) =>
      request(`/v1/uploads/multipart/${uploadId}/complete`, {
        method: "POST",
        body: JSON.stringify({ key, parts }),
      }),
    abort: (uploadId: string, key: string) =>
      request<void>(`/v1/uploads/multipart/${uploadId}/abort`, {
        method: "POST",
        body: JSON.stringify({ key }),
      }),
  },
};

/** Multipart-upload a source file to R2 (real mode) or simulate it (demo mode). */
export async function uploadSourceFile(
  projectId: string,
  file: File,
  onProgress: (pct: number) => void,
): Promise<void> {
  if (isDemoMode) {
    await demoApi.uploadFile(projectId, file, onProgress);
    return;
  }
  const upload = await realApi.uploads.create({
    project_id: projectId,
    filename: file.name,
    content_type: file.type || "application/octet-stream",
    size_bytes: file.size,
  });
  try {
    const parts: Array<{ part_number: number; etag: string }> = [];
    for (let index = 0; index < upload.part_count; index += 1) {
      const partNumber = index + 1;
      const { url } = await realApi.uploads.signPart(upload.upload_id, upload.key, partNumber);
      const start = index * upload.part_size_bytes;
      let response: Response;
      try {
        response = await fetch(url, {
          method: "PUT",
          body: file.slice(start, Math.min(file.size, start + upload.part_size_bytes)),
        });
      } catch {
        throw new Error(
          "저장소(R2) 업로드에 실패했습니다. CORS에 GitHub Pages origin이 허용되는지 확인하세요.",
        );
      }
      if (!response.ok) throw new Error(`업로드 파트 ${partNumber} 실패`);
      const etag = response.headers.get("etag");
      if (!etag) throw new Error("R2 CORS에서 ETag 응답 헤더를 노출해야 합니다.");
      parts.push({ part_number: partNumber, etag });
      onProgress(Math.round((partNumber / upload.part_count) * 100));
    }
    await realApi.uploads.complete(upload.upload_id, upload.key, parts);
  } catch (err) {
    await realApi.uploads.abort(upload.upload_id, upload.key).catch(() => undefined);
    throw err;
  }
}

type ApiShape = Omit<typeof realApi, "uploads" | "checkout" | "admin"> & {
  checkout: (kind: "subscription" | "credits") => Promise<{ url: string }>;
  uploads?: typeof realApi.uploads;
  admin?: typeof realApi.admin;
};

export const api: ApiShape = isDemoMode
  ? {
      projects: demoApi.projects,
      segments: demoApi.segments,
      jobs: demoApi.jobs,
      credits: demoApi.credits,
      checkout: demoApi.checkout,
    }
  : realApi;

export { isDemoMode };
