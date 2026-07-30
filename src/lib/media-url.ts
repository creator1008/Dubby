/** Compare media URLs ignoring signature query params so polling does not remount <video>. */
export function isSameMediaAsset(a: string | null | undefined, b: string | null | undefined): boolean {
  if (!a || !b) return false;
  try {
    return new URL(a).pathname === new URL(b).pathname;
  } catch {
    return a.split("?")[0] === b.split("?")[0];
  }
}

/** Keep the current object URL when only the signature/expiry query changed. */
export function preferStableMediaUrl(
  current: string | null | undefined,
  next: string,
): string {
  return isSameMediaAsset(current, next) ? (current as string) : next;
}

const QUALITY_WARNING_KO: Record<string, string> = {
  speech_truncated_to_prevent_overlap:
    "다음 자막과 겹치지 않도록 음성을 슬롯 길이에 맞게 잘랐습니다.",
  speech_not_extended_beyond_quality_limit:
    "음성이 짧아도 품질 한도 이하로 느리게 늘리지 않았습니다.",
  rubberband_unavailable:
    "피치 유지 타임스트레치(rubberband)를 쓸 수 없어 atempo로 맞췄습니다.",
  invalid_duration: "구간 길이가 올바르지 않아 타이밍을 기본값으로 처리했습니다.",
  diarization_provider_unavailable_single_speaker_fallback:
    "화자 분리를 사용할 수 없어 단일 화자로 처리했습니다.",
  overlapping_speakers_use_default_voice:
    "화자 구간이 겹쳐 기본 보이스를 사용했습니다.",
};

const QUALITY_WARNING_EN: Record<string, string> = {
  speech_truncated_to_prevent_overlap:
    "Speech was truncated to fit the slot and avoid overlap with the next subtitle.",
  speech_not_extended_beyond_quality_limit:
    "Speech was shorter than the slot, but was not slowed below the quality tempo limit.",
  rubberband_unavailable:
    "Pitch-preserving rubberband was unavailable; used atempo instead.",
  invalid_duration: "Invalid segment duration; used default timing.",
  diarization_provider_unavailable_single_speaker_fallback:
    "Speaker diarization unavailable; fell back to a single speaker.",
  overlapping_speakers_use_default_voice:
    "Overlapping speakers detected; used the default voice.",
};

const QUALITY_WARNING_VI: Record<string, string> = {
  speech_truncated_to_prevent_overlap:
    "Đã cắt giọng nói để vừa khung thời gian và tránh chồng với phụ đề tiếp theo.",
  speech_not_extended_beyond_quality_limit:
    "Giọng nói ngắn hơn khung, nhưng không làm chậm quá giới hạn chất lượng.",
  rubberband_unavailable:
    "Không dùng được rubberband giữ cao độ; đã dùng atempo.",
  invalid_duration: "Độ dài đoạn không hợp lệ; dùng thời gian mặc định.",
  diarization_provider_unavailable_single_speaker_fallback:
    "Không tách được người nói; xử lý như một người nói.",
  overlapping_speakers_use_default_voice:
    "Người nói chồng chéo; dùng giọng mặc định.",
};

function localizeCode(
  code: string,
  table: Record<string, string>,
): string {
  if (table[code]) return table[code];
  const match = /^(segment_\d+):(.+)$/.exec(code);
  if (match) {
    const [, segment, warning] = match;
    const detail = table[warning] ?? warning;
    return `${segment}: ${detail}`;
  }
  return code;
}

/** Human-readable quality warning for UI (codes stay stored on the project). */
export function formatQualityWarning(
  code: string,
  locale: "ko" | "en" | "vi" = "ko",
): string {
  const table =
    locale === "en"
      ? QUALITY_WARNING_EN
      : locale === "vi"
        ? QUALITY_WARNING_VI
        : QUALITY_WARNING_KO;
  return localizeCode(code, table);
}
