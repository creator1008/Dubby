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

const API_ORIGIN = (process.env.NEXT_PUBLIC_API_ORIGIN ?? "").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const supabase = getSupabase();
  const { data } = supabase
    ? await supabase.auth.getSession()
    : { data: { session: null } };
  if (!data.session) throw new ApiError("로그인이 필요합니다.", 401);
  if (!API_ORIGIN) throw new ApiError("API 주소가 설정되지 않았습니다.", 500);

  const response = await fetch(`${API_ORIGIN}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${data.session.access_token}`,
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(body?.detail ?? `요청 실패 (${response.status})`, response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
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
    sourceUrl: (id: string) =>
      request<{ url: string; expires_in: number }>(`/v1/projects/${id}/source-url`),
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
      const response = await fetch(url, {
        method: "PUT",
        body: file.slice(start, Math.min(file.size, start + upload.part_size_bytes)),
      });
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
