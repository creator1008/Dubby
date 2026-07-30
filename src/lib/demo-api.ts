"use client";

/**
 * Local demo backend: lets the whole /app flow run without the SaaS API.
 * Active only when NEXT_PUBLIC_API_ORIGIN is unset.
 */

import type { Credits, Job, LangCode, Project, Segment } from "@/lib/ui-types";
import {
  deleteLocalRun,
  gcOrphanLocalRuns,
  getLocalPipelineOrigin,
  retranslateLocalSegments,
  type LocalStep12Result,
} from "@/lib/local-step12";
import { getSupabase } from "@/lib/supabase";
import { isDubLangCode } from "@/lib/languages";

/** Append local API download hint; never mutate signed cloud object URLs. */
export function withDownloadAttachment(url: string, filename: string): string {
  // Presigned S3/R2 query params are part of the signature — do not append.
  if (/[?&](X-Amz-Signature|Signature)=/i.test(url)) {
    return url;
  }
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}download=${encodeURIComponent(filename)}`;
}

/** Force a file save even when the URL redirects cross-origin. */
export async function forceDownload(url: string, filename: string): Promise<void> {
  const downloadUrl = withDownloadAttachment(url, filename);
  const response = await fetch(downloadUrl, { redirect: "follow" });
  if (!response.ok) {
    throw new Error(`다운로드 실패 (${response.status})`);
  }
  const blob = await response.blob();
  const safeName =
    filename.replace(/[^\w.\-()\s\uAC00-\uD7A3]+/g, "_") || "dubby-output.mp4";
  const file = new File([blob], safeName, {
    type: blob.type || "video/mp4",
  });

  // iOS Safari often ignores <a download>; prefer the system share sheet.
  const nav = navigator as Navigator & {
    canShare?: (data?: ShareData) => boolean;
  };
  if (typeof nav.share === "function" && nav.canShare?.({ files: [file] })) {
    await nav.share({
      files: [file],
      title: "Dubby",
      text: safeName,
    });
    return;
  }

  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = safeName;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // Keep the object URL briefly so mobile browsers can start the download.
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 2000);
}

export const isDemoMode = !(
  process.env.NEXT_PUBLIC_API_ORIGIN ?? ""
).trim();

const STORE_KEY = "dubby.demo-state.v2";

const DEFAULT_USER_CREDIT_MINUTES = 30;

type DemoState = {
  projects: Project[];
  segments: Record<string, Segment[]>;
  jobs: Job[];
  /** @deprecated Prefer balances[userId]; kept for localStorage migration. */
  balance?: number;
  /** Per-user dubbing credit minutes (demo / local pipeline). */
  balances: Record<string, number>;
  /** Local pipeline run_id per project (scratch + R2 ``local/runs/{id}``). */
  local_run_ids: Record<string, string>;
  /** Presigned/local URLs for step-1 extracted source audio per project. */
  source_audio_urls: Record<string, string>;
  source_video_urls: Record<string, string>;
  output_urls: Record<string, string>;
};

const SAMPLE_LINES: Partial<Record<LangCode, string[]>> = {
  ko: [
    "안녕하세요, 더비를 소개합니다.",
    "영상 하나로 전 세계 시청자를 만나보세요.",
    "배경음악과 효과음은 그대로 유지됩니다.",
    "자막을 검수한 뒤 더빙을 시작할 수 있습니다.",
    "완성된 영상은 바로 다운로드할 수 있습니다.",
  ],
  en: [
    "Hello, let us introduce Dubby.",
    "Reach audiences worldwide with a single video.",
    "Background music and effects stay untouched.",
    "Review the subtitles, then start dubbing.",
    "Download the finished video right away.",
  ],
  vi: [
    "Xin chào, xin giới thiệu Dubby.",
    "Tiếp cận khán giả toàn cầu chỉ với một video.",
    "Nhạc nền và hiệu ứng được giữ nguyên.",
    "Kiểm tra phụ đề rồi bắt đầu lồng tiếng.",
    "Tải xuống video hoàn chỉnh ngay lập tức.",
  ],
};

function nowIso() {
  return new Date().toISOString();
}

function uid(prefix: string) {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

// Object URLs for the user's selected file (Before preview). Not persisted;
// after a reload the bundled demo video is used instead.
const sourceObjectUrls = new Map<string, string>();

function defaultState(): DemoState {
  return {
    projects: [],
    segments: {},
    jobs: [],
    balances: {},
    local_run_ids: {},
    source_audio_urls: {},
    source_video_urls: {},
    output_urls: {},
  };
}

let state: DemoState | null = null;
let hydratePromise: Promise<void> | null = null;

function persistLocalOnly() {
  if (!state || typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORE_KEY, JSON.stringify(state));
  } catch {
    // storage full/unavailable: demo keeps working in-memory
  }
}

function userStateSlice(st: DemoState, userId: string): DemoState {
  const projects = st.projects.filter((project) => project.owner_id === userId);
  const ids = new Set(projects.map((project) => project.id));
  const segments: Record<string, Segment[]> = {};
  const local_run_ids: Record<string, string> = {};
  const source_audio_urls: Record<string, string> = {};
  const source_video_urls: Record<string, string> = {};
  const output_urls: Record<string, string> = {};
  for (const id of ids) {
    if (st.segments[id]) segments[id] = st.segments[id];
    if (st.local_run_ids[id]) local_run_ids[id] = st.local_run_ids[id];
    if (st.source_audio_urls[id]) source_audio_urls[id] = st.source_audio_urls[id];
    if (st.source_video_urls[id]) source_video_urls[id] = st.source_video_urls[id];
    if (st.output_urls[id]) output_urls[id] = st.output_urls[id];
  }
  return {
    projects,
    segments,
    jobs: st.jobs.filter((job) => ids.has(job.project_id)),
    balances: { [userId]: st.balances[userId] ?? DEFAULT_USER_CREDIT_MINUTES },
    local_run_ids,
    source_audio_urls,
    source_video_urls,
    output_urls,
  };
}

function mergeRemoteSlice(local: DemoState, remote: DemoState, userId: string): DemoState {
  const next = { ...local };
  next.projects = [...local.projects];
  next.segments = { ...local.segments };
  next.jobs = [...local.jobs];
  next.balances = { ...local.balances };
  next.local_run_ids = { ...local.local_run_ids };
  next.source_audio_urls = { ...local.source_audio_urls };
  next.source_video_urls = { ...local.source_video_urls };
  next.output_urls = { ...local.output_urls };

  const byId = new Map(next.projects.map((project) => [project.id, project]));
  for (const project of remote.projects) {
    if (project.owner_id !== userId) continue;
    const existing = byId.get(project.id);
    if (!existing) {
      byId.set(project.id, project);
      continue;
    }
    const remoteUpdated = Date.parse(project.updated_at || project.created_at || "") || 0;
    const localUpdated = Date.parse(existing.updated_at || existing.created_at || "") || 0;
    if (remoteUpdated >= localUpdated) byId.set(project.id, project);
  }
  next.projects = Array.from(byId.values()).sort((a, b) =>
    (b.updated_at || b.created_at || "").localeCompare(a.updated_at || a.created_at || ""),
  );

  for (const [id, segs] of Object.entries(remote.segments || {})) {
    if (!next.segments[id] || (segs?.length || 0) >= (next.segments[id]?.length || 0)) {
      next.segments[id] = segs;
    }
  }
  const jobIds = new Set(next.jobs.map((job) => job.id));
  for (const job of remote.jobs || []) {
    if (!jobIds.has(job.id)) next.jobs.push(job);
  }
  Object.assign(next.local_run_ids, remote.local_run_ids || {});
  Object.assign(next.source_audio_urls, remote.source_audio_urls || {});
  Object.assign(next.source_video_urls, remote.source_video_urls || {});
  Object.assign(next.output_urls, remote.output_urls || {});
  if (remote.balances?.[userId] !== undefined) {
    // Keep the lower balance to avoid undoing charges from the other device.
    const localBal = next.balances[userId];
    const remoteBal = remote.balances[userId];
    next.balances[userId] =
      localBal === undefined ? remoteBal : Math.min(localBal, remoteBal);
  }
  return next;
}

async function pushStateToRemote(userId: string) {
  if (!state || typeof window === "undefined") return;
  try {
    const origin = getLocalPipelineOrigin();
    await fetch(`${origin}/v1/local/demo-state/${encodeURIComponent(userId)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ state: userStateSlice(state, userId) }),
    });
  } catch {
    /* tunnel may be down — localStorage still holds a copy */
  }
}

async function hydrateFromRemote() {
  if (typeof window === "undefined") return;
  if (!hydratePromise) {
    hydratePromise = (async () => {
      try {
        const userId = await requireUserId();
        const origin = getLocalPipelineOrigin();
        const response = await fetch(
          `${origin}/v1/local/demo-state/${encodeURIComponent(userId)}`,
          { cache: "no-store" },
        );
        if (!response.ok) return;
        const payload = (await response.json()) as {
          empty?: boolean;
          state?: DemoState | null;
        };
        if (payload.empty || !payload.state) {
          // Seed remote from this device so PC/phone converge next time.
          await pushStateToRemote(userId);
          return;
        }
        const local = loadState();
        state = mergeRemoteSlice(local, payload.state, userId);
        persistLocalOnly();
      } catch {
        /* keep local-only when pipeline unreachable */
      }
    })();
  }
  await hydratePromise;
  hydratePromise = null;
}

function loadState(): DemoState {
  if (state) return state;
  if (typeof window === "undefined") return defaultState();
  try {
    state = JSON.parse(window.localStorage.getItem(STORE_KEY) ?? "null") as DemoState | null;
  } catch {
    state = null;
  }
  if (!state) state = defaultState();
  if (!state.source_audio_urls) state.source_audio_urls = {};
  if (!state.source_video_urls) state.source_video_urls = {};
  if (!state.output_urls) state.output_urls = {};
  if (!state.balances) state.balances = {};
  if (!state.local_run_ids) state.local_run_ids = {};
  // Backfill run ids from asset URLs for projects created before this field existed.
  for (const project of state.projects) {
    if (state.local_run_ids[project.id]) continue;
    const runId = resolveProjectRunId(state, project.id);
    if (runId) state.local_run_ids[project.id] = runId;
  }
  // Timers don't survive reloads: settle any job that was left mid-flight.
  for (const job of state.jobs) {
    if (job.status === "queued" || job.status === "running") {
      job.status = "completed";
      job.progress = 1;
      job.message = "done";
      const project = state.projects.find((p) => p.id === job.project_id);
      if (project) {
        if (job.kind === "transcribe") {
          project.status = "ready_for_edit";
          ensureSegments(state, project);
        } else if (job.kind === "dub") {
          project.status = "completed";
          project.output_key = "demo-output";
        }
      }
    }
  }
  persistLocalOnly();
  return state;
}

function persist() {
  persistLocalOnly();
  if (!state || typeof window === "undefined") return;
  void requireUserId()
    .then((userId) => pushStateToRemote(userId))
    .catch(() => undefined);
}

function ensureSegments(st: DemoState, project: Project) {
  if (st.segments[project.id]?.length) return;
  const source =
    SAMPLE_LINES[project.source_lang as LangCode] ??
    SAMPLE_LINES.ko ??
    SAMPLE_LINES.en ??
    [];
  const target =
    SAMPLE_LINES[project.target_lang as LangCode] ??
    SAMPLE_LINES.en ??
    SAMPLE_LINES.ko ??
    [];
  st.segments[project.id] = source.map((text, idx) => ({
    id: uid("seg"),
    project_id: project.id,
    idx,
    start_ms: idx * 1900,
    end_ms: idx * 1900 + 1700,
    source_text: text,
    target_text: target[idx] ?? "",
    speaker_id: null,
    speaker_overlap: false,
  }));
}

function getProjectOrThrow(id: string): Project {
  const project = loadState().projects.find((p) => p.id === id);
  if (!project) throw new Error("프로젝트를 찾을 수 없습니다.");
  return project;
}

async function requireUserId(): Promise<string> {
  const supabase = getSupabase();
  if (!supabase) throw new Error("로그인이 필요합니다.");
  const { data } = await supabase.auth.getSession();
  const userId = data.session?.user?.id;
  if (!userId) throw new Error("로그인이 필요합니다.");
  return userId;
}

function getUserBalance(st: DemoState, userId: string): number {
  if (st.balances[userId] === undefined) {
    st.balances[userId] = DEFAULT_USER_CREDIT_MINUTES;
    persist();
  }
  return st.balances[userId];
}

function setUserBalance(st: DemoState, userId: string, minutes: number) {
  st.balances[userId] = Math.max(0, minutes);
}

/** Match real API: ceil(duration_seconds / 60) dubbing minutes. */
function dubChargeMinutes(project: Project, segments: Segment[]): number {
  let duration = project.duration_seconds ?? 0;
  if (duration <= 0 && segments.length) {
    duration = Math.max(...segments.map((row) => row.end_ms)) / 1000;
  }
  if (duration <= 0) return 1;
  return Math.max(1, Math.ceil(duration / 60));
}

async function chargeDubCredits(projectId: string): Promise<number> {
  const userId = await requireUserId();
  const st = loadState();
  const project = await requireOwnedProject(projectId);
  const charge = dubChargeMinutes(project, st.segments[projectId] ?? []);
  const balance = getUserBalance(st, userId);
  if (balance < charge) {
    throw new Error(
      `크레딧이 부족합니다. 필요 ${charge}분, 잔액 ${balance.toFixed(1)}분`,
    );
  }
  setUserBalance(st, userId, balance - charge);
  persist();
  return charge;
}

async function assertDubCredits(projectId: string): Promise<number> {
  const userId = await requireUserId();
  const st = loadState();
  const project = await requireOwnedProject(projectId);
  const charge = dubChargeMinutes(project, st.segments[projectId] ?? []);
  const balance = getUserBalance(st, userId);
  if (balance < charge) {
    throw new Error(
      `크레딧이 부족합니다. 필요 ${charge}분, 잔액 ${balance.toFixed(1)}분`,
    );
  }
  return charge;
}

async function requireOwnedProject(id: string): Promise<Project> {
  const userId = await requireUserId();
  const project = getProjectOrThrow(id);
  if (project.owner_id !== userId) {
    throw new Error("프로젝트를 찾을 수 없습니다.");
  }
  return project;
}

type JobStep = { message: string; progress: number };

const TRANSCRIBE_STEPS: JobStep[] = [
  { message: "extracting_audio", progress: 0.2 },
  { message: "asr", progress: 0.55 },
  { message: "translate", progress: 0.85 },
  { message: "done", progress: 1 },
];

const DUB_STEPS: JobStep[] = [
  { message: "stem_split", progress: 0.2 },
  { message: "voice_clone_tts", progress: 0.5 },
  { message: "mix_bgm", progress: 0.75 },
  { message: "mux", progress: 0.92 },
  { message: "done", progress: 1 },
];

function runJob(job: Job, steps: JobStep[], onDone: () => void) {
  let index = 0;
  job.status = "running";
  const tick = () => {
    const st = loadState();
    const live = st.jobs.find((j) => j.id === job.id);
    if (!live || live.status !== "running") return; // cancelled
    const step = steps[index];
    live.message = step.message;
    live.progress = step.progress;
    live.updated_at = nowIso();
    index += 1;
    if (index >= steps.length) {
      live.status = "completed";
      onDone();
      persist();
      return;
    }
    persist();
    window.setTimeout(tick, 1100);
  };
  window.setTimeout(tick, 500);
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

const LOCAL_RUN_ID_RE = /^[a-f0-9]{32}$/i;

function extractLocalRunIdFromUrl(url: string | undefined | null): string | null {
  if (!url) return null;
  const match = url.match(/\/v1\/local\/step12\/([a-f0-9]{32})\//i);
  return match?.[1]?.toLowerCase() ?? null;
}

function resolveProjectRunId(st: DemoState, projectId: string): string | null {
  const stored = st.local_run_ids[projectId];
  if (stored && LOCAL_RUN_ID_RE.test(stored)) return stored.toLowerCase();
  return (
    extractLocalRunIdFromUrl(st.source_video_urls[projectId]) ??
    extractLocalRunIdFromUrl(st.source_audio_urls[projectId]) ??
    extractLocalRunIdFromUrl(st.output_urls[projectId]) ??
    extractLocalRunIdFromUrl(st.segments[projectId]?.[0]?.audio_url) ??
    extractLocalRunIdFromUrl(st.segments[projectId]?.[0]?.dubbed_audio_url) ??
    null
  );
}

function collectActiveRunIds(st: DemoState, exceptProjectId?: string): string[] {
  const ids = new Set<string>();
  for (const project of st.projects) {
    if (exceptProjectId && project.id === exceptProjectId) continue;
    const runId = resolveProjectRunId(st, project.id);
    if (runId) ids.add(runId);
  }
  return [...ids];
}

async function cleanupProjectMedia(st: DemoState, projectId: string) {
  const runId = resolveProjectRunId(st, projectId);
  const objectUrl = sourceObjectUrls.get(projectId);
  if (objectUrl) {
    URL.revokeObjectURL(objectUrl);
    sourceObjectUrls.delete(projectId);
  }
  if (runId) {
    try {
      await deleteLocalRun(runId);
    } catch {
      // Continue with orphan GC even if the targeted delete fails.
    }
  }
  delete st.local_run_ids[projectId];
  try {
    await gcOrphanLocalRuns(collectActiveRunIds(st, projectId));
  } catch {
    // History row must still be removed even if storage GC is unavailable.
  }
}

export const demoApi = {
  projects: {
    list: async () => {
      await hydrateFromRemote();
      const userId = await requireUserId();
      return clone(
        loadState().projects.filter((project) => project.owner_id === userId),
      );
    },
    get: async (id: string) => clone(await requireOwnedProject(id)),
    create: async (
      body: Pick<
        Project,
        "title" | "source_lang" | "target_lang" | "subtitle_mode" | "tone_style" | "diarization_enabled"
      >,
    ) => {
      const userId = await requireUserId();
      const st = loadState();
      const project: Project = {
        id: uid("proj"),
        owner_id: userId,
        title: body.title,
        status: "created",
        source_lang: body.source_lang,
        target_lang: body.target_lang,
        subtitle_mode: body.subtitle_mode,
        tone_style: body.tone_style,
        diarization_enabled: body.diarization_enabled,
        duration_seconds: null,
        source_key: null,
        output_key: null,
        lipsync_output_key: null,
        quality_warnings: [],
        error: null,
        created_at: nowIso(),
        updated_at: nowIso(),
      };
      st.projects.unshift(project);
      persist();
      return clone(project);
    },
    update: async (
      id: string,
      body: Partial<Pick<Project, "tone_style" | "diarization_enabled" | "subtitle_mode">>,
    ) => {
      const project = await requireOwnedProject(id);
      Object.assign(project, body, { updated_at: nowIso() });
      persist();
      return clone(project);
    },
    remove: async (id: string) => {
      await requireOwnedProject(id);
      const st = loadState();
      await cleanupProjectMedia(st, id);
      st.projects = st.projects.filter((p) => p.id !== id);
      st.jobs = st.jobs.filter((j) => j.project_id !== id);
      delete st.segments[id];
      delete st.source_audio_urls[id];
      delete st.source_video_urls[id];
      delete st.output_urls[id];
      delete st.local_run_ids[id];
      persist();
    },
    outputUrl: async (id: string) => {
      const st = loadState();
      const project = await requireOwnedProject(id);
      if (project.status !== "completed") throw new Error("더빙 결과가 아직 없습니다.");
      const url = st.output_urls[id];
      if (!url) throw new Error("더빙 결과 파일을 찾을 수 없습니다.");
      return {
        url,
        expires_in: 3600,
      };
    },
    download: async (id: string) => {
      const st = loadState();
      const project = await requireOwnedProject(id);
      if (project.status !== "completed") throw new Error("더빙 결과가 아직 없습니다.");
      const url = st.output_urls[id];
      if (!url) throw new Error("더빙 결과 파일을 찾을 수 없습니다.");
      return {
        url: withDownloadAttachment(url, `${project.title}-dubbed.mp4`),
        expires_in: 3600,
      };
    },
    sourceUrl: async (id: string) => {
      const st = loadState();
      await requireOwnedProject(id);
      const url =
        sourceObjectUrls.get(id) ??
        st.source_video_urls[id] ??
        st.source_audio_urls[id];
      if (!url) throw new Error("원본 영상 URL을 찾을 수 없습니다.");
      return {
        url,
        expires_in: 3600,
      };
    },
    sourceFromUrl: async (id: string, url: string) => {
      const project = await requireOwnedProject(id);
      project.status = "uploaded";
      project.source_key = `demo/url/${encodeURIComponent(url.slice(0, 120))}`;
      project.updated_at = nowIso();
      persist();
      return clone(project);
    },
  },

  segments: {
    list: async (projectId: string) => {
      await requireOwnedProject(projectId);
      return clone(loadState().segments[projectId] ?? []);
    },
    update: async (
      projectId: string,
      updates: Array<Pick<Segment, "id" | "target_text"> & { source_text?: string }>,
    ) => {
      await requireOwnedProject(projectId);
      const st = loadState();
      const rows = st.segments[projectId] ?? [];
      for (const update of updates) {
        const row = rows.find((r) => r.id === update.id);
        if (!row) continue;
        row.target_text = update.target_text;
        if (update.source_text !== undefined) row.source_text = update.source_text;
      }
      persist();
      return clone(rows);
    },
    retranslate: async (
      projectId: string,
      updates: Array<Pick<Segment, "id" | "source_text">>,
    ) => {
      const project = await requireOwnedProject(projectId);
      if (!isDubLangCode(project.source_lang) || !isDubLangCode(project.target_lang)) {
        throw new Error("지원하지 않는 언어 코드입니다.");
      }
      const st = loadState();
      const rows = st.segments[projectId] ?? [];
      const payload = updates.flatMap((update) => {
        const row = rows.find((r) => r.id === update.id);
        if (!row) return [];
        row.source_text = update.source_text;
        return [
          {
            idx: row.idx,
            start_ms: row.start_ms,
            end_ms: row.end_ms,
            source_text: update.source_text,
          },
        ];
      });
      const translations = await retranslateLocalSegments(
        project.source_lang,
        project.target_lang,
        payload,
      );
      const byIdx = new Map(translations.map((row) => [row.idx, row.target_text]));
      for (const row of rows) {
        const target = byIdx.get(row.idx);
        if (target !== undefined) row.target_text = target;
      }
      persist();
      return clone(rows);
    },
  },

  jobs: {
    list: async (projectId: string) => {
      await requireOwnedProject(projectId);
      return clone(loadState().jobs.filter((j) => j.project_id === projectId));
    },
    get: async (jobId: string) => {
      const job = loadState().jobs.find((j) => j.id === jobId);
      if (!job) throw new Error("작업을 찾을 수 없습니다.");
      await requireOwnedProject(job.project_id);
      return clone(job);
    },
    create: async (projectId: string, kind: "transcribe" | "dub" | "lipsync") => {
      const st = loadState();
      const project = await requireOwnedProject(projectId);
      if (kind === "lipsync") throw new Error("립싱크는 데모 모드에서 지원하지 않습니다.");
      const job: Job = {
        id: uid("job"),
        project_id: projectId,
        kind,
        status: "queued",
        progress: 0,
        message: "queued",
        error: null,
        created_at: nowIso(),
        updated_at: nowIso(),
      };
      st.jobs.push(job);
      if (kind === "transcribe") {
        project.status = "processing";
        runJob(job, TRANSCRIBE_STEPS, () => {
          project.status = "ready_for_edit";
          project.duration_seconds = 10;
          ensureSegments(st, project);
        });
      } else {
        project.status = "dubbing";
        const ownerId = project.owner_id;
        runJob(job, DUB_STEPS, () => {
          project.status = "completed";
          project.output_key = "demo-output";
          if (ownerId) {
            const current = getUserBalance(st, ownerId);
            setUserBalance(st, ownerId, current - 0.5);
          }
        });
      }
      persist();
      return clone(job);
    },
    cancel: async (jobId: string) => {
      const st = loadState();
      const job = st.jobs.find((j) => j.id === jobId);
      if (!job) throw new Error("작업을 찾을 수 없습니다.");
      await requireOwnedProject(job.project_id);
      job.status = "failed";
      job.error = "사용자 취소";
      job.message = null;
      const project = st.projects.find((p) => p.id === job.project_id);
      if (project) {
        project.status = job.kind === "dub" ? "ready_for_edit" : "uploaded";
      }
      persist();
      return clone(job);
    },
  },

  credits: async (): Promise<Credits> => {
    const userId = await requireUserId();
    return {
      balance_minutes: getUserBalance(loadState(), userId),
      entries: [],
    };
  },

  checkout: async (): Promise<{ url: string }> => {
    throw new Error("데모 모드에서는 결제가 비활성화되어 있습니다.");
  },

  uploadFile: async (
    projectId: string,
    file: File,
    onProgress: (pct: number) => void,
  ) => {
    const project = await requireOwnedProject(projectId);
    sourceObjectUrls.set(projectId, URL.createObjectURL(file));
    for (let pct = 10; pct <= 100; pct += 15) {
      await new Promise((resolve) => window.setTimeout(resolve, 140));
      onProgress(Math.min(100, pct));
    }
    project.status = "uploaded";
    project.source_key = `demo/${file.name}`;
    project.updated_at = nowIso();
    persist();
  },

  applyStep12: async (projectId: string, result: LocalStep12Result) => {
    const st = loadState();
    const project = await requireOwnedProject(projectId);
    st.segments[projectId] = result.segments.map((item) => ({
      id: uid("seg"),
      project_id: projectId,
      idx: item.idx,
      start_ms: item.start_ms,
      end_ms: item.end_ms,
      source_text: item.text,
      target_text: item.target_text,
      speaker_id: item.speaker_id,
      speaker_overlap: false,
      audio_url: item.audio_url,
    }));
    project.status = "ready_for_edit";
    project.updated_at = nowIso();
    const maxEndMs = result.segments.reduce(
      (max, item) => Math.max(max, item.end_ms),
      0,
    );
    project.duration_seconds = maxEndMs > 0 ? maxEndMs / 1000 : null;
    st.source_audio_urls[projectId] = result.audio_url;
    st.source_video_urls[projectId] = result.source_url;
    st.local_run_ids[projectId] = result.run_id;
    persist();
    return clone(st.segments[projectId]);
  },

  applyDubVoice: async (
    projectId: string,
    outputs: Array<{ idx: number; audio_url: string }>,
  ) => {
    await chargeDubCredits(projectId);
    const st = loadState();
    const rows = st.segments[projectId] ?? [];
    const bust = `t=${Date.now()}`;
    for (const output of outputs) {
      const row = rows.find((segment) => segment.idx === output.idx);
      if (!row) continue;
      const base = output.audio_url.split("#")[0].split("?")[0];
      row.dubbed_audio_url = `${base}?${bust}`;
    }
    persist();
    return clone(rows);
  },

  /** Pre-check before expensive TTS so we fail fast on insufficient credits. */
  assertDubCredits,

  applyRender: async (
    projectId: string,
    result: { output_url: string; source_url?: string },
  ) => {
    const st = loadState();
    const project = await requireOwnedProject(projectId);
    project.status = "completed";
    project.output_key = "local/dubbed_output.mp4";
    project.updated_at = nowIso();
    st.output_urls[projectId] = result.output_url;
    if (result.source_url) {
      st.source_video_urls[projectId] = result.source_url;
    }
    persist();
    return clone(project);
  },
};
