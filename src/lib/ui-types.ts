export type LangCode = "ko" | "en" | "vi";
export type SubtitleMode = "none" | "source" | "target";
export type ToneStyle = "neutral" | "warm" | "energetic" | "serious";

export type Session = {
  user_id: string;
  email: string;
  credits_minutes: number;
};

export type Project = {
  id: string;
  title: string;
  status: string;
  source_lang: string;
  target_lang: string;
  subtitle_mode: SubtitleMode;
  tone_style: ToneStyle;
  diarization_enabled: boolean;
  duration_seconds: number | null;
  source_key: string | null;
  output_key: string | null;
  lipsync_output_key: string | null;
  quality_warnings: string[];
  error: string | null;
  created_at: string;
  updated_at: string;
};

export type Segment = {
  id: string;
  project_id: string;
  idx: number;
  start_ms: number;
  end_ms: number;
  source_text: string;
  target_text: string;
  speaker_id: string | null;
  speaker_overlap: boolean;
  /** Local step 1-2 verification clip matching this timestamp/text pair. */
  audio_url?: string;
  /** ElevenLabs preview generated from the reviewed translated text. */
  dubbed_audio_url?: string;
};

export type Job = {
  id: string;
  project_id: string;
  kind: string;
  status: string;
  progress: number;
  message: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
};

export type CreditEntry = {
  id: string;
  delta_minutes: number;
  reason: string;
  project_id: string | null;
  created_at: string;
};

export type Credits = {
  balance_minutes: number;
  entries: CreditEntry[];
};

export type AdminUser = {
  id: string;
  email: string | null;
  display_name: string | null;
  country: string | null;
  auth_provider: string | null;
  created_at: string;
  last_login_at: string | null;
  project_count: number;
  credit_balance: number;
};

export type AccessLog = {
  id: string;
  user_id: string | null;
  email: string | null;
  method: string;
  path: string;
  status_code: number;
  ip_address: string | null;
  user_agent: string | null;
  created_at: string;
};

export type AdminUserUsage = {
  profile: Omit<AdminUser, "project_count" | "credit_balance">;
  projects: Array<
    Pick<
      Project,
      "id" | "title" | "status" | "source_lang" | "target_lang" |
      "duration_seconds" | "created_at"
    >
  >;
  credits: CreditEntry[];
  credit_balance: number;
};
