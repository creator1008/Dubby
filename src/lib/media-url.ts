/** Compare media URLs ignoring signature query params so polling does not remount <video>. */
export function isSameMediaAsset(a: string | null | undefined, b: string | null | undefined): boolean {
  if (!a || !b) return false;
  try {
    return new URL(a).pathname === new URL(b).pathname;
  } catch {
    return a.split("?")[0] === b.split("?")[0];
  }
}

/** AWS/R2 presigned expiry, or null when the URL is not signed. */
export function signedUrlExpiryMs(url: string): number | null {
  try {
    const parsed = new URL(url);
    const stamp = parsed.searchParams.get("X-Amz-Date");
    const expires = parsed.searchParams.get("X-Amz-Expires");
    if (!stamp || !expires) return null;
    const year = Number(stamp.slice(0, 4));
    const month = Number(stamp.slice(4, 6));
    const day = Number(stamp.slice(6, 8));
    const hour = Number(stamp.slice(9, 11));
    const minute = Number(stamp.slice(11, 13));
    const second = Number(stamp.slice(13, 15));
    if (![year, month, day, hour, minute, second].every(Number.isFinite)) {
      return null;
    }
    const startMs = Date.UTC(year, month - 1, day, hour, minute, second);
    const ttlSec = Number(expires);
    if (!Number.isFinite(ttlSec) || ttlSec <= 0) return null;
    return startMs + ttlSec * 1000;
  } catch {
    return null;
  }
}

export function signedUrlIsFresh(
  url: string | null | undefined,
  minRemainingMs = 60_000,
): boolean {
  if (!url) return false;
  const expiry = signedUrlExpiryMs(url);
  if (expiry == null) return true;
  return expiry - Date.now() >= minRemainingMs;
}

/** Keep the current object URL when only the signature/expiry query changed. */
export function preferStableMediaUrl(
  current: string | null | undefined,
  next: string,
): string {
  if (isSameMediaAsset(current, next) && signedUrlIsFresh(current)) {
    return current as string;
  }
  return next;
}

const QUALITY_WARNING_KO: Record<string, string> = {
  speech_truncated_to_prevent_overlap:
    "속도 보정 한도를 넘어서 음성을 슬롯에 맞게 잘랐습니다.",
  speech_not_extended_beyond_quality_limit:
    "음성이 짧아도 품질 한도 이하로 느리게 늘리지 않았습니다.",
  slot_extended:
    "다음 자막 전 여유 구간에 맞춰 자막 끝 시각을 늘렸습니다.",
  speak_speed_fit:
    "다음 자막과 겹치지 않도록 음성 속도를 높여 맞췄습니다.",
  rubberband_unavailable:
    "피치 유지 타임스트레치(rubberband)를 쓸 수 없어 atempo로 맞췄습니다.",
  invalid_duration: "구간 길이가 올바르지 않아 타이밍을 기본값으로 처리했습니다.",
  diarization_provider_unavailable_single_speaker_fallback:
    "화자 분리를 사용할 수 없어 단일 화자로 처리했습니다.",
  diarization_empty_turns_single_speaker_fallback:
    "화자 분리 결과가 비어 단일 화자로 처리했습니다.",
  overlapping_speakers_use_default_voice:
    "화자 구간이 겹쳐 기본 보이스를 사용했습니다.",
  overlapping_speakers_majority_voice:
    "화자 구간이 일부 겹쳐 비중이 큰 화자 목소리를 사용했습니다.",
  voice_add_edit_limit_default_voice:
    "월간 사용 VOICE ID 한도가 초과되어 기본 음성을 사용했습니다.",
};

const QUALITY_WARNING_EN: Record<string, string> = {
  speech_truncated_to_prevent_overlap:
    "Speech was truncated after hitting the speed-fit limit.",
  speech_not_extended_beyond_quality_limit:
    "Speech was shorter than the slot, but was not slowed below the quality tempo limit.",
  slot_extended:
    "Extended the subtitle end into trailing silence before the next line.",
  speak_speed_fit:
    "Increased speaking speed so the line fits without overlapping the next subtitle.",
  rubberband_unavailable:
    "Pitch-preserving rubberband was unavailable; used atempo instead.",
  invalid_duration: "Invalid segment duration; used default timing.",
  diarization_provider_unavailable_single_speaker_fallback:
    "Speaker diarization unavailable; fell back to a single speaker.",
  diarization_empty_turns_single_speaker_fallback:
    "Diarization returned no turns; fell back to a single speaker.",
  overlapping_speakers_use_default_voice:
    "Overlapping speakers detected; used the default voice.",
  overlapping_speakers_majority_voice:
    "Overlapping speakers detected; used the majority speaker voice.",
  voice_add_edit_limit_default_voice:
    "Monthly Voice ID limit reached; used a default registered voice.",
};

const QUALITY_WARNING_VI: Record<string, string> = {
  speech_truncated_to_prevent_overlap:
    "Đã cắt giọng nói sau khi đạt giới hạn tăng tốc.",
  speech_not_extended_beyond_quality_limit:
    "Giọng nói ngắn hơn khung, nhưng không làm chậm quá giới hạn chất lượng.",
  slot_extended:
    "Đã kéo dài thời điểm kết thúc phụ đề vào khoảng lặng trước dòng tiếp theo.",
  speak_speed_fit:
    "Đã tăng tốc độ nói để vừa khung và tránh chồng với phụ đề tiếp theo.",
  rubberband_unavailable:
    "Không dùng được rubberband giữ cao độ; đã dùng atempo.",
  invalid_duration: "Độ dài đoạn không hợp lệ; dùng thời gian mặc định.",
  diarization_provider_unavailable_single_speaker_fallback:
    "Không tách được người nói; xử lý như một người nói.",
  diarization_empty_turns_single_speaker_fallback:
    "Không có kết quả tách người nói; xử lý như một người nói.",
  overlapping_speakers_use_default_voice:
    "Người nói chồng chéo; dùng giọng mặc định.",
  overlapping_speakers_majority_voice:
    "Người nói chồng chéo; dùng giọng của người nói chiếm ưu thế.",
  voice_add_edit_limit_default_voice:
    "Đã vượt hạn mức Voice ID tháng; đã dùng giọng mặc định đã đăng ký.",
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
