/** Dubbing source/target language codes and display labels. */

export const LANG_CODES = [
  "en",
  "zh",
  "ja",
  "es",
  "fr",
  "pt",
  "de",
  "ru",
  "ar",
  "ur",
  "id",
  "ms",
  "tr",
  "ta",
  "ko",
  "vi",
] as const;

export type DubLangCode = (typeof LANG_CODES)[number];

export const LANG_LABELS: Record<DubLangCode, string> = {
  en: "English",
  zh: "中文 (普通话)",
  ja: "日本語",
  es: "Español",
  fr: "Français",
  pt: "Português",
  de: "Deutsch",
  ru: "Русский",
  ar: "العربية",
  ur: "اردو",
  id: "Bahasa Indonesia",
  ms: "Bahasa Melayu",
  tr: "Türkçe",
  ta: "தமிழ்",
  ko: "한국어",
  vi: "Tiếng Việt",
};

export function isDubLangCode(value: string): value is DubLangCode {
  return (LANG_CODES as readonly string[]).includes(value);
}

/** UI locale label for dubbing language pickers (falls back to native name). */
export function localizedLangLabel(
  code: DubLangCode,
  dict: Record<string, string>,
): string {
  const key = `voiceLang_${code}`;
  return dict[key] || LANG_LABELS[code];
}
