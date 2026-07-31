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
  segment_timing: "발화 구간·타임스탬프 분할",
  correct_asr: "원본 텍스트 문맥 보정",
  translate: "통번역",
  align_translations: "번역 구간 정렬",
  voice_removed_preview: "보이스 제거 미리보기",
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
    segment_timing: "Utterance timing split",
    correct_asr: "Context ASR correction",
    translate: "Full-document translation",
    align_translations: "Align translation to segments",
    voice_removed_preview: "Voice-removed preview",
    diarization: "Speaker separation",
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
    segment_timing: "Chia đoạn lời nói / timestamp",
    correct_asr: "Hiệu chỉnh ngữ cảnh ASR",
    translate: "Dịch toàn văn",
    align_translations: "Căn chỉnh bản dịch theo đoạn",
    voice_removed_preview: "Xem trước đã gỡ giọng",
    diarization: "Phân tách người nói",
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

const PIPELINE_ERROR_KO: Record<string, string> = {
  source_too_long: "영상이 너무 깁니다. 현재 최대 10분(600초)까지 자막 추출할 수 있습니다.",
  source_too_large: "파일이 너무 큽니다. 최대 500MB까지 업로드할 수 있습니다.",
  source_unsupported_container: "지원하지 않는 영상 형식입니다. MP4를 사용해 주세요.",
  source_no_audio: "영상에 오디오가 없습니다.",
  probe_failed: "영상 정보를 읽지 못했습니다. 다른 파일로 다시 시도해 주세요.",
  worker_timeout: "작업 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.",
};

const PIPELINE_ERROR_EN: Record<string, string> = {
  source_too_long: "Video is too long. Subtitle extraction supports up to 10 minutes (600s).",
  source_too_large: "File is too large. Maximum upload size is 500MB.",
  source_unsupported_container: "Unsupported media format. Please use MP4.",
  source_no_audio: "This media has no audio track.",
  probe_failed: "Could not read media info. Try another file.",
  worker_timeout: "The job timed out. Please try again shortly.",
};

const PIPELINE_ERROR_VI: Record<string, string> = {
  source_too_long: "Video quá dài. Hiện chỉ hỗ trợ trích xuất phụ đề tối đa 10 phút (600 giây).",
  source_too_large: "File quá lớn. Giới hạn tải lên là 500MB.",
  source_unsupported_container: "Định dạng không hỗ trợ. Hãy dùng MP4.",
  source_no_audio: "Media không có âm thanh.",
  probe_failed: "Không đọc được thông tin media. Thử file khác.",
  worker_timeout: "Công việc bị quá thời gian. Vui lòng thử lại sau.",
};

/** Map worker error strings like ``source_too_long: …`` to user-facing copy. */
export function formatPipelineError(
  error: string | null | undefined,
  locale: Locale = "ko",
): string {
  const raw = (error || "").trim();
  if (!raw) {
    return locale === "en"
      ? "The job failed."
      : locale === "vi"
        ? "Công việc thất bại."
        : "작업이 실패했습니다.";
  }
  const code = raw.split(":", 1)[0]?.trim() || raw;
  const table =
    locale === "en"
      ? PIPELINE_ERROR_EN
      : locale === "vi"
        ? PIPELINE_ERROR_VI
        : PIPELINE_ERROR_KO;
  return table[code] ?? raw;
}
