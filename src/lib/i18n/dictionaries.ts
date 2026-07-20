export type Locale = "ko" | "en" | "vi";

export const LOCALES: Locale[] = ["ko", "en", "vi"];

export const LOCALE_LABELS: Record<Locale, string> = {
  ko: "한국어",
  en: "English",
  vi: "Tiếng Việt",
};

export type Dictionary = {
  pageTitle: string;
  brand: string;
  tagline: string;
  support: string;
  ctaPrimary: string;
  ctaSecondary: string;
  newDub: string;
  dubbingHistory: string;
  login: string;
  before: string;
  after: string;
  listenBefore: string;
  listenAfter: string;
  play: string;
  pause: string;
  waitlistTitle: string;
  waitlistHint: string;
  emailPlaceholder: string;
  waitlistSubmit: string;
  waitlistSuccess: string;
  waitlistError: string;
  howTitle: string;
  howSupport: string;
  steps: { title: string; body: string }[];
  langsTitle: string;
  langsBody: string;
  footer: string;
  settingsLang: string;
};

export const dictionaries: Record<Locale, Dictionary> = {
  ko: {
    pageTitle: "Dubby — AI 영상 다국어 더빙",
    brand: "Dubby",
    tagline: "영상 다국어 더빙, 한 번에",
    support:
      "번역·보이스 클로닝으로 YouTube·인강·홍보 영상을 영어·한국어·베트남어로 현지화하세요.",
    ctaPrimary: "얼리 액세스 신청",
    ctaSecondary: "데모 보기",
    newDub: "새 더빙",
    dubbingHistory: "더빙 이력",
    login: "로그인",
    before: "Before",
    after: "After",
    listenBefore: "원본 듣기",
    listenAfter: "더빙 듣기",
    play: "재생",
    pause: "일시정지",
    waitlistTitle: "출시 알림 받기",
    waitlistHint: "웹 MVP가 열리면 가장 먼저 알려드립니다.",
    emailPlaceholder: "이메일 주소",
    waitlistSubmit: "대기열 등록",
    waitlistSuccess: "등록되었습니다. 곧 소식을 전할게요.",
    waitlistError: "등록에 실패했습니다. 잠시 후 다시 시도해 주세요.",
    howTitle: "이렇게 작동합니다",
    howSupport: "업로드부터 다국어 더빙까지, 자막을 직접 다듬을 수 있습니다.",
    steps: [
      {
        title: "영상 업로드",
        body: "웹에서 영상을 올리고 목표 언어를 고릅니다.",
      },
      {
        title: "자막 검수",
        body: "자동 번역된 대사를 수정해 의미를 맞춥니다.",
      },
      {
        title: "더빙 다운로드",
        body: "원본 BGM을 살린 다국어 오디오로 받습니다.",
      },
    ],
    langsTitle: "시작 언어",
    langsBody: "영어 · 한국어 · 베트남어. 이후 전 세계 언어로 확장합니다.",
    footer: "© Dubby — AI 영상 다국어 더빙",
    settingsLang: "언어",
  },
  en: {
    pageTitle: "Dubby — AI multilingual video dubbing",
    brand: "Dubby",
    tagline: "Multilingual video dubbing, done.",
    support:
      "Localize YouTube, courses, and product videos into English, Korean, and Vietnamese with translation and voice cloning.",
    ctaPrimary: "Join early access",
    ctaSecondary: "Watch demo",
    newDub: "New dubbing",
    dubbingHistory: "Dubbing history",
    login: "Sign in",
    before: "Before",
    after: "After",
    listenBefore: "Hear original",
    listenAfter: "Hear dub",
    play: "Play",
    pause: "Pause",
    waitlistTitle: "Get launch updates",
    waitlistHint: "Be first in line when the web MVP opens.",
    emailPlaceholder: "Email address",
    waitlistSubmit: "Join waitlist",
    waitlistSuccess: "You're on the list. We'll be in touch.",
    waitlistError: "Something went wrong. Please try again.",
    howTitle: "How it works",
    howSupport: "From upload to dubbed export — with an editable transcript.",
    steps: [
      {
        title: "Upload",
        body: "Drop your video and pick a target language.",
      },
      {
        title: "Review subtitles",
        body: "Tweak auto-translations so the meaning stays sharp.",
      },
      {
        title: "Download the dub",
        body: "Get multilingual audio with original BGM preserved.",
      },
    ],
    langsTitle: "Launch languages",
    langsBody: "English, Korean, Vietnamese — more languages next.",
    footer: "© Dubby — AI multilingual video dubbing",
    settingsLang: "Language",
  },
  vi: {
    pageTitle: "Dubby — Lồng tiếng video AI đa ngôn ngữ",
    brand: "Dubby",
    tagline: "Lồng tiếng đa ngôn ngữ, một lần.",
    support:
      "Bản địa hóa video YouTube, khóa học và sản phẩm sang tiếng Anh, Hàn, Việt với dịch thuật và nhân bản giọng nói.",
    ctaPrimary: "Đăng ký early access",
    ctaSecondary: "Xem demo",
    newDub: "Lồng tiếng mới",
    dubbingHistory: "Lịch sử lồng tiếng",
    login: "Đăng nhập",
    before: "Before",
    after: "After",
    listenBefore: "Nghe gốc",
    listenAfter: "Nghe lồng tiếng",
    play: "Phát",
    pause: "Tạm dừng",
    waitlistTitle: "Nhận thông báo ra mắt",
    waitlistHint: "Ưu tiên khi web MVP mở cửa.",
    emailPlaceholder: "Địa chỉ email",
    waitlistSubmit: "Tham gia danh sách chờ",
    waitlistSuccess: "Đã đăng ký. Chúng tôi sẽ liên hệ sớm.",
    waitlistError: "Có lỗi xảy ra. Vui lòng thử lại.",
    howTitle: "Cách hoạt động",
    howSupport: "Từ tải lên đến xuất bản — kèm chỉnh sửa phụ đề.",
    steps: [
      {
        title: "Tải video",
        body: "Chọn video và ngôn ngữ đích.",
      },
      {
        title: "Duyệt phụ đề",
        body: "Chỉnh bản dịch tự động cho đúng nghĩa.",
      },
      {
        title: "Tải bản lồng tiếng",
        body: "Nhận audio đa ngôn ngữ, giữ nguyên nhạc nền.",
      },
    ],
    langsTitle: "Ngôn ngữ khởi đầu",
    langsBody: "Anh · Hàn · Việt — mở rộng toàn cầu sau này.",
    footer: "© Dubby — Lồng tiếng video AI đa ngôn ngữ",
    settingsLang: "Ngôn ngữ",
  },
};
