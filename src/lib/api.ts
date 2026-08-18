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
  SharedVoicesPage,
  UserVoice,
  VoiceFilterOptions,
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

/** Drop the in-memory healthy-origin cache (e.g. before a periodic re-check). */
export function invalidateApiOriginCache(): void {
  healthyOriginCache = null;
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

function forgetApiOrigin(): void {
  healthyOriginCache = null;
  try {
    window.localStorage.removeItem(API_ORIGIN_STORAGE_KEY);
  } catch {
    /* ignore */
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
      if (params.get("api") === "clear") {
        forgetApiOrigin();
      } else {
        const fromQuery = normalizeApiOrigin(params.get("api"));
        if (fromQuery) return rememberApiOrigin(fromQuery);
      }
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

async function readOriginPointer(
  url: string,
  timeoutMs = 4000,
): Promise<string | null> {
  const controller = new AbortController();
  const timer = globalThis.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) return null;
    const body = (await response.json()) as { api_origin?: string };
    return normalizeApiOrigin(body.api_origin);
  } catch {
    return null;
  } finally {
    globalThis.clearTimeout(timer);
  }
}

function isEphemeralOrigin(origin: string): boolean {
  return (
    /\.trycloudflare\.com$/i.test(origin) ||
    /\.loca\.lt$/i.test(origin) ||
    /\.localtunnel\.me$/i.test(origin)
  );
}

/**
 * Pull the latest tunnel URL. Prefer the same-origin Pages copy first
 * (fast on mobile), then GitHub raw as a fallback.
 */
export async function fetchPublishedApiOrigin(): Promise<string | null> {
  if (typeof window === "undefined") return null;
  const stamp = Date.now();
  const candidates = [
    `${withBasePath("/api-origin.json")}?t=${stamp}`,
    `https://raw.githubusercontent.com/creator1008/Dubby/main/public/api-origin.json?t=${stamp}`,
  ];
  for (const url of candidates) {
    const origin = await readOriginPointer(url, 4000);
    if (origin) return rememberApiOrigin(origin);
  }
  return null;
}

async function healthOk(origin: string, timeoutMs: number): Promise<boolean> {
  const controller = new AbortController();
  const timer = globalThis.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${origin}/healthz`, {
      method: "GET",
      cache: "no-store",
      mode: "cors",
      signal: controller.signal,
      headers: tunnelExtraHeaders(origin),
    });
    if (!response.ok) return false;
    const text = (await response.text()).trim();
    // Accept JSON health payloads; also accept empty/ok bodies from proxies.
    if (!text) return true;
    if (text.includes('"status"') && text.includes("ok")) return true;
    return text === "ok" || text.toLowerCase().includes("healthy");
  } catch {
    return false;
  } finally {
    globalThis.clearTimeout(timer);
  }
}

/** Extra headers for free tunnel providers that show an interstitial page. */
function tunnelExtraHeaders(origin: string): Record<string, string> {
  if (/loca\.lt$/i.test(origin) || /localtunnel\.me$/i.test(origin)) {
    return { "Bypass-Tunnel-Reminder": "1" };
  }
  return {};
}

/**
 * Prefer a live API origin. Stable builtin/current first; published pointer
 * only as fallback. All network waits are bounded so mobile never hangs on
 * "API 연결 확인 중…".
 */
function namedApiOrigin(): string | null {
  const candidates = [BUILTIN_API_ORIGIN, getApiOrigin()];
  for (const origin of candidates) {
    if (origin && /api\.dubbyai\.com$/i.test(origin) && !isEphemeralOrigin(origin)) {
      return origin;
    }
  }
  return null;
}

export async function ensureApiOrigin(timeoutMs = 8000): Promise<string> {
  // Drop sticky quick-tunnel URLs immediately — they cause Failed to fetch on
  // mobile long after the PC tunnel rotated.
  let current = getApiOrigin();
  if (current && isEphemeralOrigin(current)) {
    forgetApiOrigin();
    current = getApiOrigin();
  }

  // Stable named origin: do not block the app on /healthz. Mobile and some
  // browsers flake on the health probe even while API calls succeed.
  const named = namedApiOrigin();
  if (named) {
    return markHealthy(rememberApiOrigin(named));
  }

  const cached = healthyOriginCache;
  if (
    cached &&
    Date.now() - cached.checkedAt < HEALTHY_ORIGIN_TTL_MS &&
    cached.origin &&
    !isEphemeralOrigin(cached.origin)
  ) {
    if (await healthOk(cached.origin, Math.min(timeoutMs, 4000))) {
      return markHealthy(cached.origin);
    }
    healthyOriginCache = null;
  }

  const tried = new Set<string>();

  const tryOrigin = async (origin: string | null | undefined) => {
    if (!origin || tried.has(origin) || isEphemeralOrigin(origin)) return null;
    tried.add(origin);
    if (await healthOk(origin, timeoutMs)) {
      return markHealthy(rememberApiOrigin(origin));
    }
    return null;
  };

  {
    const hit = await tryOrigin(BUILTIN_API_ORIGIN);
    if (hit) return hit;
  }

  if (current && !isEphemeralOrigin(current)) {
    const hit = await tryOrigin(current);
    if (hit) return hit;
  }

  const published = await fetchPublishedApiOrigin();
  {
    const hit = await tryOrigin(published);
    if (hit) return hit;
  }

  if (current) forgetApiOrigin();

  healthyOriginCache = null;
  throw new ApiError(apiUnreachableMessage(), 0);
}

function apiUnreachableMessage(): string {
  const origin = getApiOrigin() || BUILTIN_API_ORIGIN || "(미설정)";
  const isNamed =
    /api\.dubbyai\.com$/i.test(origin) || /dubbyai\.com$/i.test(origin);
  if (isNamed) {
    return (
      `API 서버(${origin})에 연결할 수 없습니다. ` +
      "네트워크 상태를 확인한 뒤 화면을 새로고침해 주세요. " +
      "계속되면 PC에서 API(uvicorn)와 Cloudflare 터널이 실행 중인지 확인해 주세요."
    );
  }
  return (
    `API 서버(${origin})에 연결할 수 없습니다(Failed to fetch). ` +
    "PC에서 uvicorn과 `bash scripts/run-named-tunnel.sh` 가 켜져 있는지 확인한 뒤, " +
    "폰에서 화면을 새로고침하세요. 필요하면 ?api=https://api.dubbyai.com 으로 열어 저장하세요."
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

/** Reachability check before long extract/dub flows.
 * Named origin (api.dubbyai.com) is not blocked on a flaky /healthz probe —
 * browsers and mobile networks often fail that GET while real API calls work.
 */
export async function pingApi(timeoutMs = 8000): Promise<void> {
  const origin = namedApiOrigin() || (await ensureApiOrigin(timeoutMs));
  const named = Boolean(namedApiOrigin());
  if (await healthOk(origin, named ? 4000 : Math.max(timeoutMs, 12_000))) {
    markHealthy(rememberApiOrigin(origin));
    return;
  }
  if (named) {
    markHealthy(rememberApiOrigin(origin));
    return;
  }
  throw new ApiError(apiUnreachableMessage(), 0);
}

async function requestBlob(path: string): Promise<Blob> {
  let accessToken = await getAccessToken();
  const apiOrigin = await ensureApiOrigin();

  const controller = new AbortController();
  const timer = globalThis.setTimeout(() => controller.abort(), 10 * 60 * 1000);
  try {
    const doFetch = (token: string) =>
      fetch(`${apiOrigin}${path}`, {
        headers: {
          Authorization: `Bearer ${token}`,
          ...tunnelExtraHeaders(apiOrigin),
        },
        signal: controller.signal,
      });

    let response = await doFetch(accessToken);
    if (response.status === 401) {
      accessToken = await getAccessToken(true);
      response = await doFetch(accessToken);
    }
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new ApiError(authErrorMessage(body?.detail, response.status), response.status);
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
    globalThis.clearTimeout(timer);
  }
}

async function sleep(ms: number) {
  await new Promise((resolve) => globalThis.setTimeout(resolve, ms));
}

function authErrorMessage(detail: unknown, status: number): string {
  const raw = typeof detail === "string" ? detail : "";
  if (
    status === 401 &&
    (/invalid token/i.test(raw) ||
      /token expired/i.test(raw) ||
      /missing bearer/i.test(raw))
  ) {
    return "로그인이 만료되었거나 유효하지 않습니다. 다시 로그인해 주세요.";
  }
  return raw || `요청 실패 (${status})`;
}

/** Prefer a freshly refreshed access token before authenticated API calls. */
async function getAccessToken(forceRefresh = false): Promise<string> {
  const supabase = getSupabase();
  if (!supabase) throw new ApiError("로그인이 필요합니다.", 401);

  if (forceRefresh) {
    const { data, error } = await supabase.auth.refreshSession();
    if (error || !data.session?.access_token) {
      throw new ApiError(
        "로그인이 만료되었거나 유효하지 않습니다. 다시 로그인해 주세요.",
        401,
      );
    }
    return data.session.access_token;
  }

  const { data } = await supabase.auth.getSession();
  const session = data.session;
  if (!session?.access_token) {
    throw new ApiError("로그인이 필요합니다.", 401);
  }

  const expiresAt = session.expires_at ?? 0;
  const skewSeconds = 60;
  if (expiresAt > 0 && expiresAt * 1000 <= Date.now() + skewSeconds * 1000) {
    const { data: refreshed, error } = await supabase.auth.refreshSession();
    if (!error && refreshed.session?.access_token) {
      return refreshed.session.access_token;
    }
  }
  return session.access_token;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let accessToken = await getAccessToken();
  let apiOrigin = await ensureApiOrigin();

  const method = (init?.method || "GET").toUpperCase();
  const retries = method === "GET" || method === "HEAD" ? 4 : 3;
  let lastError: unknown;
  let refreshedForAuth = false;
  for (let attempt = 1; attempt <= retries; attempt += 1) {
    let response: Response;
    try {
      const isForm =
        typeof FormData !== "undefined" && init?.body instanceof FormData;
      response = await fetch(`${apiOrigin}${path}`, {
        ...init,
        headers: {
          Authorization: `Bearer ${accessToken}`,
          ...(init?.body && !isForm ? { "Content-Type": "application/json" } : {}),
          ...tunnelExtraHeaders(apiOrigin),
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
        await sleep(900 * attempt);
        continue;
      }
      if (err instanceof TypeError) {
        throw new ApiError(apiUnreachableMessage(), 0);
      }
      throw err;
    }
    if (response.status === 401 && !refreshedForAuth) {
      refreshedForAuth = true;
      accessToken = await getAccessToken(true);
      continue;
    }
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new ApiError(
        authErrorMessage(body?.detail, response.status),
        response.status,
      );
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
    create: (body: Pick<Project, "title" | "source_lang" | "target_lang" | "subtitle_mode" | "tone_style" | "diarization_enabled" | "dub_voice_ids" | "voice_mode" | "pipeline_version">) =>
      request<Project>("/v1/projects", { method: "POST", body: JSON.stringify(body) }),
    update: (
      id: string,
      body: Partial<Pick<Project, "tone_style" | "diarization_enabled" | "subtitle_mode" | "dub_voice_ids" | "voice_mode">>,
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
    /** Poll until URL ingest finishes (async ``source-from-url``). */
    waitUntilSourceReady: async (
      id: string,
      opts?: {
        timeoutMs?: number;
        intervalMs?: number;
        onTick?: (elapsedMs: number) => void;
      },
    ): Promise<Project> => {
      const timeoutMs = opts?.timeoutMs ?? 15 * 60 * 1000;
      const intervalMs = opts?.intervalMs ?? 2000;
      const started = Date.now();
      for (;;) {
        const project = await request<Project>(`/v1/projects/${id}`);
        if (project.source_key || project.status === "uploaded") {
          return project;
        }
        if (project.status === "failed") {
          throw new ApiError(
            project.error || "Failed to fetch video from link.",
            400,
          );
        }
        const elapsed = Date.now() - started;
        if (elapsed >= timeoutMs) {
          throw new ApiError(
            "Timed out waiting for video download from link.",
            408,
          );
        }
        opts?.onTick?.(elapsed);
        await new Promise((resolve) => setTimeout(resolve, intervalMs));
      }
    },
  },
  segments: {
    list: (projectId: string) =>
      request<Segment[]>(`/v1/projects/${projectId}/segments`),
    update: (
      projectId: string,
      segments: Array<
        Pick<Segment, "id" | "target_text"> & {
          source_text?: string;
          end_ms?: number;
          source_end_ms?: number;
          speak_speed?: number;
        }
      >,
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
  voices: {
    filters: () => request<VoiceFilterOptions>("/v1/voices/filters"),
    library: (params: {
      page?: number;
      page_size?: number;
      language?: string;
      accent?: string;
      category?: string;
      gender?: string;
      age?: string;
      search?: string;
      ui_locale?: string;
    } = {}) => {
      const qs = new URLSearchParams();
      if (params.page != null) qs.set("page", String(params.page));
      if (params.page_size != null) qs.set("page_size", String(params.page_size));
      if (params.language) qs.set("language", params.language);
      if (params.accent) qs.set("accent", params.accent);
      if (params.category) qs.set("category", params.category);
      if (params.gender) qs.set("gender", params.gender);
      if (params.age) qs.set("age", params.age);
      if (params.search) qs.set("search", params.search);
      if (params.ui_locale) qs.set("ui_locale", params.ui_locale);
      const query = qs.toString();
      return request<SharedVoicesPage>(
        `/v1/voices/library${query ? `?${query}` : ""}`,
      );
    },
    box: {
      list: () => request<UserVoice[]>("/v1/voices/box"),
      add: (body: {
        voice_id: string;
        public_owner_id: string;
        nickname: string;
        name?: string;
        description?: string | null;
        gender?: string;
        accent?: string;
        category?: string;
        language?: string;
        age?: string;
        preview_url?: string | null;
      }) =>
        request<UserVoice>("/v1/voices/box", {
          method: "POST",
          body: JSON.stringify(body),
        }),
      clone: (body: {
        nickname: string;
        language: string;
        gender: string;
        file: File;
      }) => {
        const form = new FormData();
        form.set("nickname", body.nickname);
        form.set("language", body.language);
        form.set("gender", body.gender);
        form.set("file", body.file);
        return request<UserVoice>("/v1/voices/box/clone", {
          method: "POST",
          body: form,
        });
      },
      remove: (id: string) =>
        request<void>(`/v1/voices/box/${id}`, { method: "DELETE" }),
    },
  },
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
      voices: demoApi.voices,
      checkout: demoApi.checkout,
    }
  : realApi;

export { isDemoMode };
