import type { DubLangCode } from "@/lib/languages";

export type LangCode = DubLangCode;
export type SubtitleMode = "none" | "source" | "target";
export type ToneStyle =
  | "sad"
  | "angry"
  | "whisper"
  | "excited"
  | "energetic"
  | "calm"
  | "cheerful";

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
  /** Original ASR end; preserved when dub/speak-rate extends ``end_ms``. */
  source_end_ms?: number;
  source_text: string;
  target_text: string;
  speaker_id: string | null;
  speaker_overlap: boolean;
  /** Local step 1-2 verification clip matching this timestamp/text pair. */
  audio_url?: string;
  /** ElevenLabs preview generated from the reviewed translated text. */
  dubbed_audio_url?: string;
  /** Auto-fitted / editor TTS speak speed for this segment (1.0 = natural). */
  speak_speed?: number;
  /** Reset target for the speak-rate control (always natural 1.0). */
  baseline_speak_speed?: number;
  /** Speak rate used when the current dubbed preview clip was synthesized. */
  clip_speak_speed?: number;
  /** Detected emotion tone applied when synthesizing this segment's dub. */
  emotion_tone?: ToneStyle | string;
};

/** Keep preview clips when the API omits optional voice fields.
 * Prefer server speak_speed after save; reset target is always natural 1.0×.
 * Drop stale dubbed_audio_url when target_text changes (unless the server
 * already returned a fresh clip URL).
 */
export function mergeSegmentVoiceFields(prev: Segment[], next: Segment[]): Segment[] {
  const byId = new Map(prev.map((row) => [row.id, row]));
  return next.map((row) => {
    const prior = byId.get(row.id);
    if (!prior) return { ...row, baseline_speak_speed: 1 };
    const speed =
      typeof row.speak_speed === "number"
        ? row.speak_speed
        : prior.speak_speed ?? 1;
    let sourceEnd = row.source_end_ms ?? prior.source_end_ms;
    if (
      (typeof sourceEnd !== "number" || sourceEnd <= row.start_ms) &&
      Math.abs(speed - 1) >= 0.001
    ) {
      const translationMs = Math.max(120, row.end_ms - row.start_ms);
      sourceEnd = row.start_ms + Math.round(translationMs * Math.max(0.5, speed));
    }
    if (typeof sourceEnd !== "number" || sourceEnd <= row.start_ms) {
      sourceEnd = prior.source_end_ms ?? row.end_ms;
    }
    const textChanged = prior.target_text !== row.target_text;
    const toneChanged =
      (prior.emotion_tone || "") !== (row.emotion_tone || "");
    const staleVoice = textChanged || toneChanged;
    return {
      ...row,
      dubbed_audio_url: staleVoice
        ? row.dubbed_audio_url
        : (row.dubbed_audio_url ?? prior.dubbed_audio_url),
      audio_url: row.audio_url ?? prior.audio_url,
      source_end_ms: sourceEnd,
      speak_speed: speed,
      baseline_speak_speed: 1,
      clip_speak_speed:
        typeof row.clip_speak_speed === "number"
          ? row.clip_speak_speed
          : staleVoice
            ? undefined
            : prior.clip_speak_speed,
      emotion_tone: row.emotion_tone ?? prior.emotion_tone,
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
