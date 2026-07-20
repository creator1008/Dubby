import type { Locale } from "./i18n/dictionaries";

const MESSAGE_LABELS_KO: Record<string, string> = {
  queued: "대기 중",
  extracting_audio: "오디오 추출",
  measuring_duration: "영상 길이 확인",
  stem_split: "배경음 분리",
  refine_timing: "타임스탬프 보정",
  extract_vocals: "원본 목소리 추출",
  inpaint_ambient: "주변 소리 복원",
  voice_clone_tts: "목소리 복제·더빙 합성",
  prepare_asr: "음성 인식 준비",
  asr: "음성 인식 (STT)",
  translate: "번역",
  diarization: "화자 구분",
  tts: "음성 합성 (TTS)",
  mix_bgm: "더빙 믹스",
  mux: "영상 합성",
  burn_subtitles: "자막 합성",
  lipsync_submit: "립싱크 생성",
  lipsync_upload: "립싱크 결과 저장",
  done: "완료",
};

const STATUS_LABELS_KO: Record<string, string> = {
  queued: "대기",
  running: "진행 중",
  completed: "완료",
  failed: "실패",
};

const KIND_LABELS_KO: Record<string, string> = {
  transcribe: "자막 생성",
  dub: "더빙",
  lipsync: "프리미엄 립싱크",
};

const MESSAGE_LABELS: Record<Locale, Record<string, string>> = {
  ko: MESSAGE_LABELS_KO,
  en: {
    queued: "Queued", extracting_audio: "Extracting audio",
    measuring_duration: "Checking duration", stem_split: "Separating background",
    refine_timing: "Refining timestamps", extract_vocals: "Extracting source voice",
    inpaint_ambient: "Restoring ambience", voice_clone_tts: "Cloning voice and synthesizing",
    prepare_asr: "Preparing speech recognition", asr: "Speech recognition (STT)",
    translate: "Translation", diarization: "Speaker separation",
    tts: "Speech synthesis (TTS)", mix_bgm: "Mixing dub",
    mux: "Compositing video", burn_subtitles: "Rendering subtitles",
    lipsync_submit: "Creating lip sync", lipsync_upload: "Saving lip-sync result",
    done: "Done",
  },
  vi: {
    queued: "Đang chờ", extracting_audio: "Trích xuất âm thanh",
    measuring_duration: "Kiểm tra thời lượng", stem_split: "Tách âm nền",
    refine_timing: "Căn chỉnh mốc thời gian", extract_vocals: "Trích xuất giọng gốc",
    inpaint_ambient: "Khôi phục âm thanh nền", voice_clone_tts: "Nhân bản và tổng hợp giọng",
    prepare_asr: "Chuẩn bị nhận dạng giọng nói", asr: "Nhận dạng giọng nói (STT)",
    translate: "Dịch", diarization: "Phân tách người nói",
    tts: "Tổng hợp giọng nói (TTS)", mix_bgm: "Trộn bản lồng tiếng",
    mux: "Ghép video", burn_subtitles: "Ghép phụ đề",
    lipsync_submit: "Tạo đồng bộ khẩu hình", lipsync_upload: "Lưu kết quả khẩu hình",
    done: "Hoàn tất",
  },
};

const STATUS_LABELS: Record<Locale, Record<string, string>> = {
  ko: STATUS_LABELS_KO,
  en: { queued: "Queued", running: "Running", completed: "Completed", failed: "Failed" },
  vi: { queued: "Đang chờ", running: "Đang chạy", completed: "Hoàn tất", failed: "Lỗi" },
};

const KIND_LABELS: Record<Locale, Record<string, string>> = {
  ko: KIND_LABELS_KO,
  en: { transcribe: "Subtitles", dub: "Dubbing", lipsync: "Premium lip sync" },
  vi: { transcribe: "Phụ đề", dub: "Lồng tiếng", lipsync: "Đồng bộ khẩu hình cao cấp" },
};

export function jobKindLabel(kind: string, locale: Locale = "ko") {
  return KIND_LABELS[locale][kind] ?? kind;
}

export function jobStatusLabel(status: string, locale: Locale = "ko") {
  return STATUS_LABELS[locale][status] ?? status;
}

export function jobMessageLabel(message: string | null | undefined, locale: Locale = "ko") {
  if (!message) {
    return locale === "en" ? "Processing" : locale === "vi" ? "Đang xử lý" : "처리 중";
  }
  return MESSAGE_LABELS[locale][message] ?? message;
}
