"use client";

/**
 * Local demo backend: lets the whole /app flow (upload → transcribe →
 * edit → dub → before/after) run in the browser with no API server,
 * so screens can be developed and verified locally first.
 * Active when NEXT_PUBLIC_LOCAL_PIPELINE=true, or when no SaaS API origin
 * is configured.
 */

import type { Credits, Job, LangCode, Project, Segment } from "@/lib/ui-types";
import type { LocalStep12Result } from "@/lib/local-step12";

export const isDemoMode =
  (process.env.NEXT_PUBLIC_LOCAL_PIPELINE ?? "").trim() === "true" ||
  !(process.env.NEXT_PUBLIC_API_ORIGIN ?? "").trim();

const STORE_KEY = "dubby.demo-state.v1";

type DemoState = {
  projects: Project[];
  segments: Record<string, Segment[]>;
  jobs: Job[];
  balance: number;
  /** Presigned/local URLs for step-1 extracted source audio per project. */
  source_audio_urls: Record<string, string>;
  source_video_urls: Record<string, string>;
  output_urls: Record<string, string>;
};

const SAMPLE_LINES: Record<LangCode, string[]> = {
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
    balance: 42,
    source_audio_urls: {},
    source_video_urls: {},
    output_urls: {},
  };
}

let state: DemoState | null = null;

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
  persist();
  return state;
}

function persist() {
  if (!state || typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORE_KEY, JSON.stringify(state));
  } catch {
    // storage full/unavailable: demo keeps working in-memory
  }
}

function ensureSegments(st: DemoState, project: Project) {
  if (st.segments[project.id]?.length) return;
  const source = SAMPLE_LINES[(project.source_lang as LangCode)] ?? SAMPLE_LINES.ko;
  const target = SAMPLE_LINES[(project.target_lang as LangCode)] ?? SAMPLE_LINES.en;
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

export const demoApi = {
  projects: {
    list: async () => clone(loadState().projects),
    get: async (id: string) => clone(getProjectOrThrow(id)),
    create: async (
      body: Pick<
        Project,
        "title" | "source_lang" | "target_lang" | "subtitle_mode" | "tone_style" | "diarization_enabled"
      >,
    ) => {
      const st = loadState();
      const project: Project = {
        id: uid("proj"),
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
      const project = getProjectOrThrow(id);
      Object.assign(project, body, { updated_at: nowIso() });
      persist();
      return clone(project);
    },
    remove: async (id: string) => {
      const st = loadState();
      st.projects = st.projects.filter((p) => p.id !== id);
      st.jobs = st.jobs.filter((j) => j.project_id !== id);
      delete st.segments[id];
      delete st.source_audio_urls[id];
      delete st.source_video_urls[id];
      delete st.output_urls[id];
      persist();
    },
    download: async (id: string) => {
      const st = loadState();
      const project = getProjectOrThrow(id);
      if (project.status !== "completed") throw new Error("더빙 결과가 아직 없습니다.");
      const url = st.output_urls[id] ?? "/demo-after.mp4";
      const separator = url.includes("?") ? "&" : "?";
      return {
        url: `${url}${separator}download=${encodeURIComponent(`${project.title}-dubbed.mp4`)}`,
        expires_in: 3600,
      };
    },
    sourceUrl: async (id: string) => {
      const st = loadState();
      getProjectOrThrow(id);
      return {
        url:
          sourceObjectUrls.get(id) ??
          st.source_video_urls[id] ??
          st.source_audio_urls[id] ??
          "/demo-before.mp4",
        expires_in: 3600,
      };
    },
  },

  segments: {
    list: async (projectId: string) => clone(loadState().segments[projectId] ?? []),
    update: async (
      projectId: string,
      updates: Array<Pick<Segment, "id" | "target_text"> & { source_text?: string }>,
    ) => {
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
  },

  jobs: {
    list: async (projectId: string) =>
      clone(loadState().jobs.filter((j) => j.project_id === projectId)),
    get: async (jobId: string) => {
      const job = loadState().jobs.find((j) => j.id === jobId);
      if (!job) throw new Error("작업을 찾을 수 없습니다.");
      return clone(job);
    },
    create: async (projectId: string, kind: "transcribe" | "dub" | "lipsync") => {
      const st = loadState();
      const project = getProjectOrThrow(projectId);
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
        runJob(job, DUB_STEPS, () => {
          project.status = "completed";
          project.output_key = "demo-output";
          st.balance = Math.max(0, st.balance - 0.5);
        });
      }
      persist();
      return clone(job);
    },
    cancel: async (jobId: string) => {
      const st = loadState();
      const job = st.jobs.find((j) => j.id === jobId);
      if (!job) throw new Error("작업을 찾을 수 없습니다.");
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

  credits: async (): Promise<Credits> => ({
    balance_minutes: loadState().balance,
    entries: [],
  }),

  checkout: async (): Promise<{ url: string }> => {
    throw new Error("데모 모드에서는 결제가 비활성화되어 있습니다.");
  },

  uploadFile: async (
    projectId: string,
    file: File,
    onProgress: (pct: number) => void,
  ) => {
    const project = getProjectOrThrow(projectId);
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
    const project = getProjectOrThrow(projectId);
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
    st.source_audio_urls[projectId] = result.audio_url;
    st.source_video_urls[projectId] = result.source_url;
    persist();
    return clone(st.segments[projectId]);
  },

  applyDubVoice: async (
    projectId: string,
    outputs: Array<{ idx: number; audio_url: string }>,
  ) => {
    const st = loadState();
    const rows = st.segments[projectId] ?? [];
    for (const output of outputs) {
      const row = rows.find((segment) => segment.idx === output.idx);
      if (row) row.dubbed_audio_url = output.audio_url;
    }
    persist();
    return clone(rows);
  },

  applyRender: async (
    projectId: string,
    result: { output_url: string },
  ) => {
    const st = loadState();
    const project = getProjectOrThrow(projectId);
    project.status = "completed";
    project.output_key = "local/dubbed_output.mp4";
    project.updated_at = nowIso();
    st.output_urls[projectId] = result.output_url;
    persist();
    return clone(project);
  },
};
