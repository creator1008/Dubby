import type { DubLangCode } from "@/lib/languages";

export type LangCode = DubLangCode;
export type SubtitleMode = "none" | "source" | "target";
export type ToneStyle = "neutral" | "warm" | "energetic" | "serious";

export type Session = {
  user_id: string;
  email: string;
  credits_minutes: number;
};

export type Project = {
  id: string;
  /** Authenticated Supabase user id that owns this project. */
  owner_id?: string | null;
  title: string;
  status: string;
  source_lang: string;
  target_lang: string;
  subtitle_mode: SubtitleMode;
  tone_style: ToneStyle;
  diarization_enabled: boolean;
  /** Ordered ElevenLabs voice IDs for speaker 1, 2, … */
  dub_voice_ids?: string[];
  /** voice_box = My Voice Box; auto_clone = Instant Voice Clone per speaker */
  voice_mode?: "voice_box" | "auto_clone";
  /** Pipeline architecture version (2.0 = original-base mix). */
  pipeline_version?: string;
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
  /** Auto-fitted TTS speak speed for this segment (1.0 = natural). */
  speak_speed?: number;
  /** Original auto-fitted speed; used by the reset control. */
  baseline_speak_speed?: number;
};

/** Keep preview clips when the API omits optional voice fields.
 * Prefer server speak_speed/end_ms after save (auto-refit).
 */
export function mergeSegmentVoiceFields(prev: Segment[], next: Segment[]): Segment[] {
  const byId = new Map(prev.map((row) => [row.id, row]));
  return next.map((row) => {
    const prior = byId.get(row.id);
    if (!prior) return row;
    return {
      ...row,
      dubbed_audio_url: row.dubbed_audio_url ?? prior.dubbed_audio_url,
      speak_speed:
        typeof row.speak_speed === "number" ? row.speak_speed : prior.speak_speed,
      baseline_speak_speed:
        typeof row.baseline_speak_speed === "number"
          ? row.baseline_speak_speed
          : typeof row.speak_speed === "number"
            ? row.speak_speed
            : prior.baseline_speak_speed,
    };
  });
}

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

export type SharedVoice = {
  public_owner_id: string;
  voice_id: string;
  name: string;
  description?: string | null;
  gender: string;
  accent: string;
  category: string;
  language?: string | null;
  age: string;
  preview_url?: string | null;
};

export type SharedVoicesPage = {
  voices: SharedVoice[];
  has_more: boolean;
  total_count: number;
  page: number;
};

export type VoiceFilterOptions = {
  languages: string[];
  accents_by_language: Record<string, string[]>;
  genders: string[];
  ages: string[];
  categories: string[];
};

export type UserVoice = {
  id: string;
  nickname: string;
  elevenlabs_voice_id: string;
  shared_voice_id: string;
  public_owner_id: string;
  name: string;
  description: string;
  gender: string;
  accent: string;
  category: string;
  language: string;
  age: string;
  preview_url?: string | null;
  created_at: string;
};

export type AdminUser = {
  id: string;
  email: string | null;
  display_name: string | null;
  country: string | null;
  auth_provider: string | null;
  created_at: string;
  last_login_at: string | null;
  is_active: boolean;
  deactivated_at: string | null;
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

export type AdminJobRow = {
  id: string;
  kind: string;
  status: string;
  progress: number;
  charged_minutes: number;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  project_id: string;
  project_title: string | null;
};

export type AdminPaymentPurchase = {
  id: string;
  delta_minutes: number;
  reason: string;
  external_reference: string | null;
  created_at: string;
};

export type AdminSubscription = {
  stripe_subscription_id: string;
  stripe_customer_id: string;
  status: string;
  price_id: string | null;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
  created_at: string;
  updated_at: string;
};

export type AdminUserUsage = {
  profile: Omit<AdminUser, "project_count" | "credit_balance">;
  projects: Array<
    Pick<
      Project,
      | "id"
      | "title"
      | "status"
      | "source_lang"
      | "target_lang"
      | "duration_seconds"
      | "created_at"
    >
  >;
  jobs: AdminJobRow[];
  credits: CreditEntry[];
  payments: {
    purchases: AdminPaymentPurchase[];
    subscriptions: AdminSubscription[];
  };
  credit_balance: number;
};
