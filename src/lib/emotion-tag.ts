export const EMOTION_CLASSES = [
  "Happy",
  "Calm",
  "Confident",
  "Hopeful",
  "Sad",
  "Angry",
  "Excited",
  "Nervous",
  "Serious",
  "Playful",
  "Worried",
  "Embarrassed",
  "Lonely",
] as const;

export type EmotionClass = (typeof EMOTION_CLASSES)[number];
export type EmotionIntensity = "low" | "medium" | "high";
export type EmotionVolume = "whisper" | "soft" | "normal" | "loud" | "shout";

export type EmotionTag = {
  class: EmotionClass;
  intensity: EmotionIntensity;
  volume: EmotionVolume;
  custom?: string | null;
  source?: "auto" | "user" | "default";
};

const CLASS_ALIASES: Record<string, EmotionClass> = {
  happy: "Happy",
  cheerful: "Happy",
  happily: "Happy",
  warm: "Happy",
  calm: "Calm",
  neutral: "Calm",
  confident: "Confident",
  hopeful: "Hopeful",
  sad: "Sad",
  angry: "Angry",
  excited: "Excited",
  energetic: "Excited",
  nervous: "Nervous",
  nervously: "Nervous",
  serious: "Serious",
  playful: "Playful",
  playfully: "Playful",
  worried: "Worried",
  embarrassed: "Embarrassed",
  lonely: "Lonely",
  whisper: "Calm",
};

const HINTS: Array<[string[], EmotionClass]> = [
  [["기쁘", "행복", "명랑", "유쾌", "vui", "hạnh phúc", "happy", "cheerful"], "Happy"],
  [["차분", "평온", "침착", "bình tĩnh", "calm"], "Calm"],
  [["자신", "당당", "tự tin", "confident"], "Confident"],
  [["희망", "기대", "hy vọng", "hopeful"], "Hopeful"],
  [["슬프", "우울", "buồn", "sad"], "Sad"],
  [["화난", "화나", "분노", "짜증", "tức", "giận", "angry"], "Angry"],
  [["흥분", "신나", "활기", "hào hứng", "excited", "energetic"], "Excited"],
  [["긴장", "초조", "lo lắng", "nervous"], "Nervous"],
  [["진지", "심각", "nghiêm", "serious"], "Serious"],
  [["장난", "익살", "tinh nghịch", "playful"], "Playful"],
  [["걱정", "우려", "lo", "worried"], "Worried"],
  [["부끄", "창피", "xấu hổ", "embarrassed"], "Embarrassed"],
  [["외로", "쓸쓸", "cô đơn", "lonely"], "Lonely"],
];

export function defaultEmotionTag(fallback: EmotionClass = "Calm"): EmotionTag {
  return {
    class: fallback,
    intensity: "medium",
    volume: "normal",
    custom: null,
    source: "default",
  };
}

export function classFromCustomText(text: string): EmotionClass | null {
  const lowered = (text || "").trim().toLowerCase();
  if (!lowered) return null;
  if (CLASS_ALIASES[lowered]) return CLASS_ALIASES[lowered];
  for (const [needles, klass] of HINTS) {
    if (needles.some((needle) => lowered.includes(needle))) return klass;
  }
  return null;
}

export function parseEmotionTag(
  value: EmotionTag | string | null | undefined,
  fallback: EmotionClass = "Calm",
): EmotionTag {
  if (value && typeof value === "object") {
    const klass =
      (EMOTION_CLASSES as readonly string[]).includes(String(value.class || ""))
        ? (value.class as EmotionClass)
        : CLASS_ALIASES[String(value.class || value.custom || "").toLowerCase()] ||
          classFromCustomText(String(value.custom || value.class || "")) ||
          fallback;
    return {
      class: klass,
      intensity:
        value.intensity === "low" || value.intensity === "high"
          ? value.intensity
          : "medium",
      volume: (
        ["whisper", "soft", "normal", "loud", "shout"] as EmotionVolume[]
      ).includes(value.volume as EmotionVolume)
        ? (value.volume as EmotionVolume)
        : "normal",
      custom: value.custom || null,
      source: value.source || "auto",
    };
  }
  const raw = String(value || "").trim();
  if (!raw) return defaultEmotionTag(fallback);
  if (raw.toLowerCase() === "whisper") {
    return {
      class: "Calm",
      intensity: "medium",
      volume: "whisper",
      custom: null,
      source: "auto",
    };
  }
  return {
    class: CLASS_ALIASES[raw.toLowerCase()] || classFromCustomText(raw) || fallback,
    intensity: "medium",
    volume: "normal",
    custom: CLASS_ALIASES[raw.toLowerCase()] ? null : raw,
    source: "auto",
  };
}

/** UI-language free text → English emotion_tag stored on the segment. */
export function emotionTagFromUiInput(
  raw: string,
  current?: EmotionTag | null,
): EmotionTag {
  const text = raw.trim();
  const hinted = classFromCustomText(text);
  const klass =
    (EMOTION_CLASSES as readonly string[]).includes(text)
      ? (text as EmotionClass)
      : hinted || current?.class || "Calm";
  const custom =
    (EMOTION_CLASSES as readonly string[]).includes(text) || CLASS_ALIASES[text.toLowerCase()]
      ? null
      : text || null;
  return {
    class: klass,
    intensity: current?.intensity || "medium",
    volume: text.toLowerCase() === "whisper" || text === "속삭임" ? "whisper" : current?.volume || "normal",
    custom,
    source: "user",
  };
}

export function toneFromEmotionTag(tag: EmotionTag | null | undefined): string {
  const parsed = parseEmotionTag(tag);
  if (parsed.volume === "whisper") return "whisper";
  return parsed.class.toLowerCase();
}
