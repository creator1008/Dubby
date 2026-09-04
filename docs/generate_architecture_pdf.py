"""Generate Dubby 시스템 아키텍처 정의서 (Korean PDF)."""

from __future__ import annotations

from pathlib import Path

from fpdf import FPDF

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "Dubby_시스템_아키텍처_정의서.pdf"

FONT_REG = Path(r"C:\Windows\Fonts\malgun.ttf")
FONT_BOLD = Path(r"C:\Windows\Fonts\malgunbd.ttf")
FONT_MONO = Path(r"C:\Windows\Fonts\consola.ttf")

NAVY = (26, 54, 93)
BLUE = (43, 108, 176)
SLATE = (74, 85, 104)
INK = (26, 32, 44)
RULE = (203, 213, 225)
BOX_BG = (247, 250, 252)
COVER_NAVY = (15, 40, 74)

DOC_ID = "DUBBY-SAD-001"
DOC_TITLE = "시스템 아키텍처 정의서"
PRODUCT = "Dubby"
APP_VER = "3.0.12"
PIPE_VER = "3.0"
DOC_DATE = "2026-09-04"
DOC_STATUS = "인수인계용 / 내부"


class ArchPDF(FPDF):
    def __init__(self) -> None:
        super().__init__(format="A4", unit="mm")
        self.set_title(f"{PRODUCT} {DOC_TITLE}")
        self.set_author("Dubby")
        self.set_creator("Dubby architecture generator")
        self.set_lang("ko")
        self.add_font("Malgun", "", str(FONT_REG))
        self.add_font("Malgun", "B", str(FONT_BOLD))
        if FONT_MONO.exists():
            self.add_font("Consolas", "", str(FONT_MONO))
            self.mono = "Consolas"
        else:
            self.mono = "Malgun"
        self.set_auto_page_break(auto=True, margin=20)
        self._skip_header = False

    def header(self) -> None:
        if self._skip_header or self.page_no() == 1:
            return
        self.set_draw_color(*NAVY)
        self.set_line_width(0.6)
        self.line(16, 12, 194, 12)
        self.set_font("Malgun", "", 8)
        self.set_text_color(*SLATE)
        self.set_xy(16, 6)
        self.cell(90, 5, f"{PRODUCT}  {DOC_TITLE}", align="L")
        self.cell(88, 5, DOC_ID, align="R")
        self.set_y(16)

    def footer(self) -> None:
        if self._skip_header or self.page_no() == 1:
            return
        self.set_y(-14)
        self.set_draw_color(*RULE)
        self.set_line_width(0.3)
        self.line(16, self.get_y(), 194, self.get_y())
        self.set_font("Malgun", "", 8)
        self.set_text_color(*SLATE)
        self.cell(90, 8, f"제품 {APP_VER}  ·  파이프라인 {PIPE_VER}  ·  {DOC_DATE}", align="L")
        self.cell(88, 8, f"{self.page_no() - 1}", align="R")

    def cover(self) -> None:
        self._skip_header = True
        self.add_page()
        self.set_fill_color(*COVER_NAVY)
        self.rect(0, 0, 210, 297, "F")
        self.set_fill_color(*BLUE)
        self.rect(0, 0, 8, 297, "F")
        self.set_text_color(255, 255, 255)
        self.set_font("Malgun", "", 12)
        self.set_xy(28, 42)
        self.cell(0, 8, "HANDOVER  ·  INTERNAL")
        self.set_font("Malgun", "B", 22)
        self.set_xy(28, 78)
        self.cell(0, 12, PRODUCT)
        self.set_font("Malgun", "B", 28)
        self.set_xy(28, 94)
        self.multi_cell(160, 14, DOC_TITLE)
        self.set_draw_color(255, 255, 255)
        self.set_line_width(0.5)
        self.line(28, 128, 120, 128)
        self.set_font("Malgun", "", 11)
        meta = [
            ("문서번호", DOC_ID),
            ("제품 버전", APP_VER),
            ("파이프라인 버전", PIPE_VER),
            ("작성일", DOC_DATE),
            ("상태", DOC_STATUS),
            ("근거", "저장소 코드 기준 (추측 인프라 없음)"),
        ]
        y = 140
        for k, v in meta:
            self.set_xy(28, y)
            self.set_font("Malgun", "", 10)
            self.set_text_color(160, 184, 210)
            self.cell(42, 7, k)
            self.set_font("Malgun", "B", 10)
            self.set_text_color(255, 255, 255)
            self.cell(110, 7, v)
            y += 9
        self.set_xy(28, 250)
        self.set_font("Malgun", "", 9)
        self.set_text_color(160, 184, 210)
        self.multi_cell(
            160,
            5,
            "이 문서는 Dubby의 런타임·배포·네트워크·저장소·인증 경계를 정의한다.\n"
            "비즈니스 규칙, DB 스키마 상세, 프롬프트 전문은 후속 문서에서 다룬다.",
        )
        self._skip_header = False

    def h1(self, text: str) -> None:
        self.add_page()
        self.set_fill_color(*NAVY)
        self.rect(16, self.get_y(), 3.2, 9, "F")
        self.set_xy(22, self.get_y())
        self.set_font("Malgun", "B", 16)
        self.set_text_color(*NAVY)
        self.cell(0, 9, text)
        self.ln(12)

    def h2(self, text: str) -> None:
        if self.get_y() > 250:
            self.add_page()
        self.ln(2)
        self.set_font("Malgun", "B", 12)
        self.set_text_color(*BLUE)
        self.cell(0, 8, text)
        self.ln(8)
        self.set_draw_color(*RULE)
        self.set_line_width(0.25)
        self.line(16, self.get_y() - 1.5, 194, self.get_y() - 1.5)

    def p(self, text: str) -> None:
        self.set_font("Malgun", "", 10)
        self.set_text_color(*INK)
        self.multi_cell(0, 5.6, text)
        self.ln(2.2)

    def bullet(self, items: list[str]) -> None:
        self.set_font("Malgun", "", 10)
        self.set_text_color(*INK)
        for item in items:
            x = self.l_margin
            y = self.get_y()
            if y > 272:
                self.add_page()
                y = self.get_y()
            self.set_xy(x, y)
            self.cell(5, 5.6, "•")
            self.set_xy(x + 5, y)
            self.multi_cell(178, 5.6, item)
            self.ln(0.6)
        self.ln(1.5)

    def note(self, title: str, body: str) -> None:
        if self.get_y() > 245:
            self.add_page()
        self.set_font("Malgun", "B", 9)
        h_title = float(self.multi_cell(170, 5.2, title, dry_run=True, output="HEIGHT") or 5.2)
        self.set_font("Malgun", "", 9)
        h_body = float(self.multi_cell(170, 5.2, body, dry_run=True, output="HEIGHT") or 5.2)
        h = h_title + h_body + 6
        y = self.get_y()
        self.set_fill_color(*BOX_BG)
        self.set_draw_color(*BLUE)
        self.set_line_width(0.7)
        self.rect(16, y, 178, h, "F")
        self.line(16, y, 16, y + h)
        self.set_xy(20, y + 2.5)
        self.set_font("Malgun", "B", 9)
        self.set_text_color(*NAVY)
        self.multi_cell(170, 5.2, title)
        self.set_x(20)
        self.set_font("Malgun", "", 9)
        self.set_text_color(*INK)
        self.multi_cell(170, 5.2, body)
        self.set_y(y + h + 4)

    def diagram(self, lines: str) -> None:
        if self.get_y() > 220:
            self.add_page()
        text = lines.strip("\n")
        n = text.count("\n") + 1
        h = n * 4.2 + 8
        if self.get_y() + h > 277:
            self.add_page()
        y = self.get_y()
        self.set_fill_color(*BOX_BG)
        self.set_draw_color(*RULE)
        self.rect(16, y, 178, h, "DF")
        self.set_xy(19, y + 3)
        self.set_font(self.mono, "", 7.4)
        self.set_text_color(45, 55, 72)
        self.multi_cell(172, 4.2, text)
        self.set_y(y + h + 3)

    def table(self, headers: list[str], rows: list[list[str]], widths: list[float]) -> None:
        if self.get_y() > 250:
            self.add_page()
        self.set_font("Malgun", "B", 8.5)
        self.set_fill_color(*NAVY)
        self.set_text_color(255, 255, 255)
        x0 = 16
        self.set_x(x0)
        for h, w in zip(headers, widths):
            self.cell(w, 7, h, border=0, fill=True)
        self.ln()
        self.set_font("Malgun", "", 8.2)
        fill = False
        for row in rows:
            # estimate height
            line_h = 4.8
            max_lines = 1
            for cell, w in zip(row, widths):
                max_lines = max(max_lines, max(1, self.get_string_width(cell) // (w - 2) + 1))
            row_h = max(7.2, max_lines * line_h + 2)
            if self.get_y() + row_h > 277:
                self.add_page()
                self.set_font("Malgun", "B", 8.5)
                self.set_fill_color(*NAVY)
                self.set_text_color(255, 255, 255)
                self.set_x(x0)
                for h, w in zip(headers, widths):
                    self.cell(w, 7, h, border=0, fill=True)
                self.ln()
                self.set_font("Malgun", "", 8.2)
            y = self.get_y()
            self.set_fill_color(241, 245, 249) if fill else self.set_fill_color(255, 255, 255)
            self.set_text_color(*INK)
            x = x0
            for cell, w in zip(row, widths):
                self.rect(x, y, w, row_h, "F")
                self.set_xy(x + 1, y + 1.2)
                self.multi_cell(w - 2, line_h, cell)
                x += w
            self.set_y(y + row_h)
            fill = not fill
        self.ln(4)


def build() -> None:
    if not FONT_REG.exists() or not FONT_BOLD.exists():
        raise SystemExit(f"Malgun Gothic not found: {FONT_REG}")

    pdf = ArchPDF()
    pdf.set_margins(16, 16, 16)
    pdf.cover()

    # ----- 1 -----
    pdf.h1("1. 문서 개요")
    pdf.h2("1.1 목적")
    pdf.p(
        "이 문서는 Dubby의 시스템 경계를 한 장의 인수인계 기준으로 고정한다. "
        "누가 어디에 배포되고, 요청이 어떤 경로로 흐르며, 파일이 어디를 지나지 않는지, "
        "인증이 무엇을 신뢰하는지를 코드와 배포 설정에 근거해 기술한다."
    )
    pdf.h2("1.2 범위")
    pdf.bullet(
        [
            "포함: 프론트엔드·API·워커·리버스 프록시·DNS/도메인·인증·객체 스토리지·외부 SaaS 연동점·운영 헬스체크·환경 변수 이름.",
            "제외: 세그먼트 비즈니스 규칙의 세부, 프롬프트 전문, 테이블 DDL과 마이그레이션 이력. 이들은 각각 「비즈니스 로직 및 기능 명세서」, 「AI 에이전트 및 프롬프트 명세서」, 「데이터베이스/파이프라인 설계서」에서 다룬다.",
            "사실의 기준: 저장소 현재 코드(제품 버전 3.0.12, PIPELINE_VERSION 3.0). 문서에 없는 클라우드 구성은 가정하지 않는다.",
        ]
    )
    pdf.h2("1.3 대상 독자")
    pdf.bullet(
        [
            "인수받는 개발자·운영자: 배포 단위와 재시작 범위를 파악한다.",
            "프론트/백엔드 작업자: CORS·업로드 경로·JWT 경계를 오해하지 않는다.",
            "모바일 담당: 웹과 네이티브의 결제·파일 저장 경로가 갈라지는 지점을 본다.",
        ]
    )
    pdf.h2("1.4 관련 산출물")
    pdf.table(
        ["문서", "역할"],
        [
            ["본 문서 (SAD-001)", "런타임·배포·네트워크·저장 경계"],
            ["비즈니스 로직 및 기능 명세서 (후속)", "제품 기능, 크레딧, 더빙/추출 UX"],
            ["데이터베이스/파이프라인 설계서 (후속)", "스키마, 잡 상태, 미디어 처리 단계"],
            ["AI 에이전트 및 프롬프트 명세서 (후속)", "STT·번역·TTS 프롬프트와 모델"],
            ["docs/CUSTOM_DOMAIN.md", "도메인·DNS·터널 운영 메모"],
            ["api/.env.example", "환경 변수 이름(값 없음)"],
        ],
        [72, 106],
    )
    pdf.h2("1.5 용어")
    pdf.table(
        ["용어", "의미"],
        [
            ["UI / Pages", "Next.js static export(out/). 서버 런타임 없음."],
            ["API", "FastAPI(uvicorn). 인증·CRUD·프리사인·잡 Enqueue."],
            ["Worker", "파이프라인 실행 프로세스. jobs 테이블을 폴링한다."],
            ["Caddy", "Lightsail에서 TLS 종료 및 reverse_proxy."],
            ["R2", "Cloudflare R2. 브라우저가 직접 multipart PUT."],
            ["healthz", "프로세스 생존. DB/R2를 건드리지 않음. 버전은 API 이미지."],
            ["readyz", "DB ping. 실패 시 503."],
        ],
        [36, 142],
    )

    # ----- 2 -----
    pdf.h1("2. 시스템 개요")
    pdf.p(
        "Dubby는 원본 영상을 올리고, 대본을 추출·수정한 뒤, 대상 언어로 더빙해 내려받는 제품이다. "
        "웹은 정적 사이트이고, 무거운 미디어와 AI 작업은 API가 아닌 워커와 외부 공급자가 수행한다. "
        "브라우저와 앱은 100MB급 원본을 API 본문으로 보내지 않는다."
    )
    pdf.h2("2.1 한 줄 구조")
    pdf.diagram(
        """
  [브라우저 / Capacitor 앱]
           |  HTTPS (정적 자산)
           v
  dubbyai.com  (GitHub Pages 또는 Cloudflare Pages, out/)
           |
           |  HTTPS + Bearer JWT  (JSON만, 본문 작음)
           v
  api.dubbyai.com  --Caddy-->  dubby-api:8000 (FastAPI)
           |                         |
           |                    jobs enqueue
           |                         v
           |                   dubby-worker  (ffmpeg / Demucs / AI)
           |
           +--> 브라우저 presigned PUT/GET ---->  Cloudflare R2
           +--> Supabase Auth JWT 검증, Postgres(또는 PostgREST)
           +--> Gemini / OpenAI / ElevenLabs / Stripe / RevenueCat
"""
    )
    pdf.h2("2.2 설계 원칙")
    pdf.bullet(
        [
            "정적 UI: Next.js output: \"export\". 서버 컴포넌트 런타임·API Route에 의존하지 않는다.",
            "API는 오케스트레이션만: 파일 바이트를 프록시하지 않고 R2 프리사인을 발급한다.",
            "워커는 별 이미지: Demucs/torch는 메모리·이미지 크기가 커서 API와 분리한다.",
            "사용자 id는 클라이언트를 믿지 않는다. 모든 소유권은 Supabase JWT sub에서 온다.",
            "프로덕션은 PIPELINE_MODE=real만 허용한다. mock은 개발·테스트 전용이다.",
        ]
    )
    pdf.h2("2.3 제품·배포 버전")
    pdf.p(
        "프론트 package.json과 API api/app/__init__.py의 __version__은 3.0.12로 맞춘다. "
        "파이프라인 호환 표식 PIPELINE_VERSION은 \"3.0\"이다. "
        "공개 /healthz의 version 필드는 API 컨테이너에 들어간 그 파일이다. "
        "워커만 재빌드하면 healthz 버전은 바뀌지 않는다."
    )

    # ----- 3 -----
    pdf.h1("3. 시스템 컨텍스트")
    pdf.h2("3.1 행위자")
    pdf.table(
        ["행위자", "진입점", "비고"],
        [
            ["웹 사용자", "https://dubbyai.com", "Stripe 결제, 데스크톱 저장 피커"],
            ["모바일 웹", "동일 도메인", "저장 피커 생략, 공유/다운로드 경로"],
            ["네이티브 앱", "com.dubby.app", "Capacitor, RevenueCat 결제"],
            ["관리자", "/v1/admin", "JWT role 기반. 크레딧·로그"],
            ["결제 공급자", "웹훅", "Stripe / RevenueCat. 서명·헤더 검증"],
        ],
        [36, 48, 94],
    )
    pdf.h2("3.2 외부 시스템")
    pdf.table(
        ["시스템", "역할", "호출 주체"],
        [
            ["Supabase Auth", "가입·세션 JWT 발급", "브라우저 SDK / API 검증"],
            ["Postgres (Supabase)", "프로젝트·세그먼트·잡·크레딧", "API·워커 Repository"],
            ["Cloudflare R2", "원본·산출·보이스 샘플", "브라우저 PUT, 워커 GET/PUT"],
            ["Gemini 3.7 Flash", "기본 STT·번역", "워커"],
            ["OpenAI", "STT/번역 fallback, 화자분리", "워커"],
            ["ElevenLabs", "보이스 클론·TTS", "워커·API(보이스 라이브러리)"],
            ["Stripe", "웹 구독·크레딧 팩", "API"],
            ["RevenueCat", "iOS/Android 인앱결제", "앱 + API 웹훅"],
            ["yt-dlp 대상 사이트", "URL 수집", "API (ffmpeg+Node 22)"],
            ["Sync.so", "선택 립싱크", "워커 (기본 disabled)"],
        ],
        [42, 62, 74],
    )
    pdf.h2("3.3 신뢰 경계")
    pdf.bullet(
        [
            "공개: 정적 UI, /healthz, /readyz, 결제 웹훅(서명 필요).",
            "인증 필요: /v1/* (헬스 제외). Authorization: Bearer <Supabase JWT>.",
            "브라우저 → R2: 프리사인 URL만. 키 prefix는 서버가 users/{user_id}/... 로 강제한다.",
            "워커 → DB: 서비스 측 Repository. 사용자 JWT를 들고 다니지 않는다.",
            "Caddy는 CORS 헤더를 넣지 않는다. 중복 ACAO는 브라우저가 거절한다.",
        ]
    )

    # ----- 4 -----
    pdf.h1("4. 논리 아키텍처")
    pdf.h2("4.1 계층")
    pdf.diagram(
        """
  Presentation     Next.js 16 static UI (React 19), i18n ko/en/vi
                   Capacitor shell (webDir=out), billingPlatform 분기
  -----------------------------------------------------------------
  Edge / TLS       GitHub Pages 또는 CF Pages (UI)
                   Caddy h1/h2 only  (API, Lightsail)
                   로컬: Cloudflare named tunnel → localhost:8000
  -----------------------------------------------------------------
  Application      FastAPI create_app()
                   CORS → AccessLogMiddleware → routers
                   JwtVerifier, R2Storage, Repository
  -----------------------------------------------------------------
  Pipeline         worker.runner  claim_next_job
                   PIPELINE_HANDLERS: transcribe | dub | lipsync
                   Engine (real | mock)
  -----------------------------------------------------------------
  Data             Postgres asyncpg  또는  supabase_rest
                   R2 objects, Demucs model volume
"""
    )
    pdf.h2("4.2 프론트엔드")
    pdf.p(
        "next.config.ts는 output: \"export\", trailingSlash, 비최적화 이미지를 사용한다. "
        "NEXT_PUBLIC_SITE_URL 기본값은 https://dubbyai.com 이다. "
        "사이트 URL에 dubbyai.com이 포함되거나 DUBBY_CUSTOM_DOMAIN=true이면 basePath를 비운다. "
        "커스텀 도메인 없는 GitHub Pages만 /Dubby prefix를 쓴다."
    )
    pdf.p(
        "API 오리진은 NEXT_PUBLIC_API_ORIGIN, 쿼리 ?api=, localStorage 순으로 src/lib/api.ts가 해석한다. "
        "오리진이 비어 있으면 src/lib/demo-api.ts 로컬 데모 백엔드로 떨어진다. "
        "세션은 @supabase/supabase-js PKCE, persist session, anon key이다."
    )
    pdf.h2("4.3 API")
    pdf.p(
        "api/app/main.py의 create_app()이 FastAPI를 만든다. "
        "수명주기에서 Repository·R2Storage·JwtVerifier를 app.state에 올린다. "
        "프로덕션에서는 /docs를 끈다. 라우터는 health, projects, segments, jobs, credits, "
        "voices, billing, uploads, admin이다."
    )
    pdf.p(
        "AccessLogMiddleware는 /v1/* 히트를 DB에 남긴다. CORSMiddleware보다 안쪽에 두어 "
        "500 응답에서도 ACAO가 떨어지지 않게 한다. Starlette BaseHTTPMiddleware는 이 이유로 쓰지 않는다."
    )
    pdf.h2("4.4 워커")
    pdf.p(
        "python -m app.worker.runner 가 jobs를 폴링한다. 기본 동시성 1(WORKER_CONCURRENCY). "
        "핸들러는 transcribe, dub, lipsync. 하트비트 파일로 컨테이너 HEALTHCHECK를 하고, "
        "오래 멈춘 running 잡은 리퍼가 실패 처리한다. 잡 타임아웃 기본 3600초."
    )
    pdf.h2("4.5 설정 모듈")
    pdf.p(
        "모든 배포 값은 api/app/config.py Settings에만 있다. 코드에 자격 증명을 넣지 않는다. "
        "APP_ENV=production이면 Supabase, DB, R2, Stripe price id, "
        "(Gemini STT/번역 사용 시) GEMINI_API_KEY, PIPELINE_MODE=real을 강제한다."
    )

    # ----- 5 -----
    pdf.h1("5. 물리 배포 아키텍처")
    pdf.h2("5.1 프로덕션 목표 형상 (Lightsail)")
    pdf.p(
        "infra/docker-compose.yml 은 단일 Lightsail 인스턴스용이다. 서비스 세 개: api, worker, caddy. "
        "네트워크는 bridge, IPv6 비활성. Docker DNS가 짧은 호스트명 api의 AAAA 조회에서 SERVFAIL을 내면 "
        "Caddy(Go)가 깨지므로 API의 네트워크 별칭은 dubby-api 이다. Caddyfile은 dubby-api:8000 만 본다."
    )
    pdf.diagram(
        """
  Internet
     |  :80 / :443
     v
  [caddy:2-alpine]  TLS(ACME)  protocols h1 h2  (h3 금지)
     |  reverse_proxy  health_uri /healthz
     v
  [dubby-api:latest]  uvicorn :8000  workers=1
     |  DB / R2 / Stripe / JWT
     |
  [dubby-worker:latest]  memory ~6g  stop_grace 5m
     |  volume demucs-models, worker-scratch
     v
  ffmpeg, rubberband, Noto, CPU torch/Demucs
"""
    )
    pdf.table(
        ["서비스", "이미지/타깃", "역할"],
        [
            ["api", "Dockerfile target api", "uvicorn, ffmpeg, Node 22(yt-dlp JS)"],
            ["worker", "Dockerfile target worker", "파이프라인, Demucs CPU, Noto 자막"],
            ["caddy", "caddy:2-alpine", "TLS, 본문 10MB, gzip, Alt-Svc 제거"],
        ],
        [32, 52, 94],
    )
    pdf.note(
        "운영 주의 — 이미지 경계",
        "파이프라인·프롬프트 수정은 워커 이미지. healthz version·라우터 수정은 API 이미지. "
        "둘 다 같은 api/ 컨텍스트로 빌드하지만 target이 다르다. compose up --build 시 필요한 타깃을 함께 올린다."
    )
    pdf.h2("5.2 UI 배포")
    pdf.bullet(
        [
            "빌드: npm run build → out/. wrangler.jsonc pages_build_output_dir은 ./out.",
            "GitHub Actions .github/workflows/deploy-pages.yml: main 푸시 시 GitHub Pages. GITHUB_PAGES=true, DUBBY_CUSTOM_DOMAIN=true, NEXT_PUBLIC_SITE_URL=https://dubbyai.com, 빈 basePath. out/CNAME = dubbyai.com.",
            "Cloudflare Pages: wrangler pages deploy out 로도 동일 산출물을 올릴 수 있다. 공개 도메인은 dubbyai.com.",
            "GitHub secrets: NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY, NEXT_PUBLIC_API_ORIGIN. 서비스 롤 키는 UI에 넣지 않는다.",
        ]
    )
    pdf.h2("5.3 로컬·터널 형상")
    pdf.p(
        "개발 PC에서는 API를 :8000 uvicorn으로 띄우고, Cloudflare named tunnel로 api.dubbyai.com에 붙일 수 있다. "
        "START_SERVICES.md와 scripts/run-named-tunnel.sh가 이 경로다. "
        "UI의 NEXT_PUBLIC_API_ORIGIN은 https://api.dubbyai.com 을 가리킨다. "
        "터널은 로컬 작업용이며, Lightsail Caddy 스택과 동시에 같은 DNS를 먹으면 충돌한다."
    )
    pdf.h2("5.4 모바일")
    pdf.p(
        "capacitor.config.ts: appId com.dubby.app, webDir out. Android scheme https. "
        "billingPlatform()은 ios/android면 revenuecat, 그 외 stripe. "
        "네이티브는 웹과 같은 API·R2를 쓰되 결제와 파일 저장만 갈라진다."
    )
    pdf.h2("5.5 DNS (공개 도메인)")
    pdf.p(
        "docs/CUSTOM_DOMAIN.md 기준 공개 이름은 dubbyai.com(UI), api.dubbyai.com(API)이다. "
        "UI apex는 GitHub Pages(creator1008.github.io) CNAME. "
        "API는 Lightsail A레코드+Caddy이거나, 로컬 개발 시 터널 CNAME(주황 구름)이다. "
        "현재 운영 중인 쪽이 어느 DNS인지는 대시보드를 기준으로 하고, 문서에 없는 IP를 적지 않는다."
    )

    # ----- 6 -----
    pdf.h1("6. 구성 요소 명세")
    pdf.h2("6.1 정적 UI")
    pdf.table(
        ["항목", "값"],
        [
            ["기술", "Next.js 16.2.10, React 19, Tailwind 4"],
            ["산출", "out/ (정적 HTML/JS/CSS)"],
            ["상태", "브라우저. Supabase 세션 + API JSON"],
            ["i18n", "ko / en / vi"],
            ["대상 언어 목록", "src/lib/languages.ts (ko, vi, en, zh, ja 등)"],
            ["데모 모드", "API origin 미설정 시 demo-api.ts"],
        ],
        [40, 138],
    )
    pdf.h2("6.2 FastAPI")
    pdf.table(
        ["항목", "값"],
        [
            ["엔트리", "uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"],
            ["헬스", "컨테이너 HEALTHCHECK → /healthz"],
            ["첨부 바이너리", "ffmpeg, Node 22 (yt-dlp 챌린지)"],
            ["업로드 한도(프록시)", "Caddy request_body max_size 10MB — JSON·웹훅용"],
            ["원본 한도(제품)", "최대 30분, 500MB (워커/검증). R2 경유"],
        ],
        [40, 138],
    )
    pdf.h2("6.3 Worker")
    pdf.table(
        ["항목", "값"],
        [
            ["엔트리", "python -m app.worker.runner"],
            ["동시성", "기본 1 (Demucs 메모리)"],
            ["메모리 리밋", "WORKER_MEMORY_LIMIT 기본 6g"],
            ["종료", "stop_grace_period 5m (진행 중 잡 drain)"],
            ["모델 캐시", "volume demucs-models → TORCH_HOME"],
            ["헬스", "WORKER_HEARTBEAT_FILE 60초 이내 mtime"],
        ],
        [40, 138],
    )
    pdf.h2("6.4 Caddy")
    pdf.bullet(
        [
            "{$DUBBY_API_DOMAIN} 사이트 블록. ACME 메일 {$ACME_EMAIL}.",
            "servers.protocols: h1 h2. HTTP/3 비활성. header -Alt-Svc.",
            "이유: Chrome이 Alt-Svc h3를 오래 캐시하고, Lightsail QUIC/UDP 실패가 net::ERR_FAILED + CORS처럼 보인다.",
            "reverse_proxy dubby-api:8000, health_uri /healthz, 30초.",
            "Access-Control-* 를 Caddy에 두지 말 것.",
        ]
    )
    pdf.h2("6.5 로컬 전용 프로세스")
    pdf.p(
        "api/app/local_step12.py 는 포트 8002의 로컬 step1/2 실험용이다. npm run dev:step12. "
        "프로덕션 이미지 CMD에 포함되지 않는다. 인수 시 이 프로세스를 Lightsail에 올리지 않는다."
    )

    # ----- 7 -----
    pdf.h1("7. API 표면")
    pdf.p("인증이 없는 엔드포인트는 헬스뿐이다. 나머지는 Bearer JWT. 웹훅은 별도 서명.")
    pdf.table(
        ["영역", "경로 접두", "책임"],
        [
            ["health", "/healthz, /readyz", "생존 / DB 준비"],
            ["projects", "/v1/projects", "CRUD, URL 수집, 다운로드 URL"],
            ["segments", "/v1/projects/.../segments", "자막 조회·수정·재번역"],
            ["jobs", "/v1/projects/{id}/jobs", "transcribe/dub/lipsync enqueue"],
            ["uploads", "/v1/uploads/multipart", "R2 create/sign/complete/abort"],
            ["credits", "/v1/credits", "잔액·원장 조회"],
            ["voices", "/v1/voices", "라이브러리·마이보이스·IVC"],
            ["billing", "/v1/billing", "Stripe Checkout, 웹훅"],
            ["admin", "/v1/admin", "사용자·크레딧 조정·액세스 로그"],
        ],
        [32, 62, 84],
    )
    pdf.h2("7.1 업로드 프로토콜")
    pdf.bullet(
        [
            "POST /v1/uploads/multipart → upload_id, key, 파트 크기(최대 16MiB).",
            "POST .../parts → 파트별 프리사인. 브라우저가 R2에 PUT, ETag 수집.",
            "POST .../complete 또는 abort. API는 바이트를 받지 않는다.",
            "키는 항상 서버가 users/{user_id}/... 로 만들고, 이후 요청마다 prefix를 재검증한다.",
        ]
    )
    pdf.h2("7.2 잡")
    pdf.p(
        "API는 잡 행만 넣는다. 실행은 워커. dub/lipsync는 프로젝트 duration 기준으로 크레딧을 선과금한다. "
        "동일 프로젝트에 활성 잡이 있으면 ActiveJobExistsError → 409."
    )

    # ----- 8 -----
    pdf.h1("8. 데이터와 스토리지")
    pdf.h2("8.1 Repository")
    pdf.p(
        "api/app/db/ 의 Repository 인터페이스만 사용한다. "
        "postgres(asyncpg, Lightsail 권장, 풀 1–5) 또는 supabase_rest(PostgREST + service role). "
        "스키마 상세는 후속 DB 설계서. 이 문서는 접근 경로만 고정한다."
    )
    pdf.h2("8.2 R2 키 레이아웃")
    pdf.diagram(
        """
  users/{user_id}/projects/{project_id}/source/{filename}
  users/{user_id}/projects/{project_id}/outputs/...
  users/{user_id}/projects/{project_id}/meta/...
  users/{user_id}/voices/inbox/{uuid}/{filename}

  엔드포인트: https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com
  서명: S3v4 path-style. 빈 바디 CRC 체크섬을 프리사인에 붙이지 않음
        (브라우저 PUT 실바이트와 불일치하면 R2가 파트를 거절)
  파트: 최소 5MiB(마지막 제외), 상한 16MiB. 구 env의 64MiB는 config가 잘라 낸다.
  프리사인 만료: 업로드 3600s, 다운로드 60–3600s.
"""
    )
    pdf.h2("8.3 워커 로컬 디스크")
    pdf.p(
        "잡마다 scratch 디렉터리. 컨테이너는 worker-scratch 볼륨을 /tmp에 붙인다. "
        "Demucs 가중치는 demucs-models 볼륨에 남겨 재생성 비용을 줄인다. "
        "완료·실패 후 scratch는 정리한다."
    )

    # ----- 9 -----
    pdf.h1("9. 인증과 권한")
    pdf.h2("9.1 JWT")
    pdf.p(
        "api/app/auth.py JwtVerifier. 헤더 Authorization: Bearer. "
        "SUPABASE_JWT_SECRET가 있고 알고리즘이 HS256이면 대칭키 검증. "
        "아니면 프로젝트 JWKS(ES256/RS256). audience 기본값 authenticated. "
        "AuthenticatedUser.id 는 토큰 sub뿐이며, 바디의 user id는 소유권 근거가 아니다."
    )
    pdf.h2("9.2 관리자")
    pdf.p("AdminUser 의존성. JWT role 등으로 /v1/admin을 연다. 일반 사용자 크레딧 원장은 서버만 기록한다.")
    pdf.h2("9.3 웹훅")
    pdf.bullet(
        [
            "Stripe: stripe_webhook_secret 서명 검증.",
            "RevenueCat: revenuecat_webhook_auth_header 와 Authorization 일치.",
            "웹훅은 브라우저 CORS 경로가 아니다. Caddy 10MB면 충분하다.",
        ]
    )
    pdf.h2("9.4 프론트 키")
    pdf.p(
        "UI는 anon key와 Supabase URL만 가진다. service role, R2 시크릿, Stripe secret, Gemini 키는 "
        "Lightsail env(또는 로컬 api/.env)에만 둔다. 저장소에 .env를 커밋하지 않는다."
    )

    # ----- 10 -----
    pdf.h1("10. 네트워크와 보안")
    pdf.h2("10.1 CORS")
    pdf.p(
        "credentialed CORS. 와일드카드 Origin/* 헤더는 쓰지 않는다. "
        "config.cors_origin_list는 설정값에 더해 dubbyai.com, www, dubby.pages.dev, "
        "GitHub Pages, localhost:3000, https://localhost, capacitor://localhost 를 항상 포함한다. "
        "정규식은 *.pages.dev 와 localhost 포트를 추가로 받는다. "
        "허용 메서드 GET/POST/PUT/PATCH/DELETE/OPTIONS. "
        "헤더 Authorization, Content-Type, Accept, Origin, X-Requested-With, Bypass-Tunnel-Reminder. max_age 86400."
    )
    pdf.h2("10.2 본문 크기와 업로드")
    pdf.note(
        "Caddy 10MB ≠ 원본 업로드 한도",
        "프로젝트 소스와 보이스 클론 미디어는 프리사인 R2로 간다. "
        "Caddy 한도를 100MB로 올리는 것은 설계가 아니다. JSON·웹훅만 프록시를 지난다."
    )
    pdf.h2("10.3 HTTP/3")
    pdf.p(
        "API 사이트는 h3를 켜지 않는다. 이미 Alt-Svc를 받은 Chrome은 오래 QUIC을 시도할 수 있으므로 "
        "Caddy가 Alt-Svc를 지운다. UI(정적 호스트)의 HTTP/3는 이 제약과 별개다."
    )
    pdf.h2("10.4 컨테이너 네트워크")
    pdf.bullet(
        [
            "enable_ipv6: false. GODEBUG=netdns=go (Caddy).",
            "API 호스트 별칭 dubby-api. 짧은 이름 api 로 프록시하지 말 것.",
            "API 포트 8000은 expose만. 공인은 443. 디버그 시에만 8000 매핑.",
        ]
    )

    # ----- 11 -----
    pdf.h1("11. 주요 요청 흐름")
    pdf.h2("11.1 로그인")
    pdf.diagram(
        """
  UI  --PKCE-->  Supabase Auth  --> 세션 JWT (local persist)
  UI  --Authorization: Bearer-->  API JwtVerifier
  실패: 401. 클라이언트 user_id 헤더는 없음.
"""
    )
    pdf.h2("11.2 원본 업로드 후 프로젝트")
    pdf.diagram(
        """
  1. UI POST /v1/uploads/multipart          (메타만)
  2. UI PUT  part → R2  (16MiB 단위, 반복)
  3. UI POST /v1/uploads/multipart/complete
  4. UI POST /v1/projects  (source_key 등 JSON)
  5. (선택) POST .../source-from-url  → API yt-dlp+ffmpeg → R2
"""
    )
    pdf.h2("11.3 추출 (transcribe)")
    pdf.diagram(
        """
  UI POST /v1/projects/{id}/jobs  { kind: transcribe }
  API: jobs 행 queued
  Worker: claim → R2 GET source → ffmpeg
        → STT(Gemini 기본) → 세그먼트 persist → progress report
  UI: 잡 폴링, 세그먼트 에디터
"""
    )
    pdf.h2("11.4 더빙 (dub)")
    pdf.diagram(
        """
  UI POST jobs { kind: dub }
  API: duration 기반 크레딧 선과금, queued
  Worker: 번역(필요 시) → ElevenLabs TTS → Demucs 음성 제거
        → mix_no_vocals_in_mask(기본 0.8) 믹스 → mux → R2 outputs
  UI: 프리사인 GET으로 재생/저장 (API가 파일을 스트리밍하지 않음)
"""
    )
    pdf.h2("11.5 결제")
    pdf.p(
        "웹: Stripe Checkout. 성공 웹훅이 크레딧 원장에 분. "
        "네이티브: RevenueCat → 웹훅 Authorization 일치 시 분 부여. "
        "가입 시 signup_credit_minutes 기본 30."
    )

    # ----- 12 -----
    pdf.h1("12. 설정 (이름만, 값 없음)")
    pdf.p("값은 이 문서에 적지 않는다. 이름과 용도만 고정한다. 실값은 infra/.env 또는 api/.env.")
    pdf.h2("12.1 프로덕션 필수")
    pdf.table(
        ["변수", "용도"],
        [
            ["APP_ENV=production", "부트 시 필수값 검증, /docs 비활성"],
            ["PIPELINE_MODE=real", "production에서 mock 거부"],
            ["SUPABASE_URL", "JWKS·Auth"],
            ["DATABASE_URL", "db_backend=postgres 일 때"],
            ["R2_ACCOUNT_ID / ACCESS / SECRET", "프리사인"],
            ["STRIPE_SECRET_KEY, WEBHOOK, PRICE_ID 둘", "웹 결제"],
            ["GEMINI_API_KEY", "STT/번역 provider=gemini 일 때"],
            ["DUBBY_API_DOMAIN, ACME_EMAIL", "Caddy TLS"],
        ],
        [78, 100],
    )
    pdf.h2("12.2 UI 빌드 타임")
    pdf.table(
        ["변수", "용도"],
        [
            ["NEXT_PUBLIC_SITE_URL", "OAuth 리다이렉트. localhost 폴백 금지"],
            ["NEXT_PUBLIC_API_ORIGIN", "브라우저가 호출할 API"],
            ["NEXT_PUBLIC_SUPABASE_URL / ANON_KEY", "클라이언트 Auth"],
            ["DUBBY_CUSTOM_DOMAIN / GITHUB_PAGES", "basePath 결정"],
        ],
        [78, 100],
    )
    pdf.h2("12.3 자주 조정하는 런타임")
    pdf.bullet(
        [
            "WORKER_CONCURRENCY, WORKER_MEMORY_LIMIT",
            "DEMUCS_MODEL (기본 htdemucs), DEMUCS_DEVICE (기본 cpu)",
            "MIX_NO_VOCALS_IN_MASK (기본 0.8)",
            "STT_PROVIDER / TRANSLATION_PROVIDER (기본 gemini)",
            "ELEVENLABS_TTS_MODEL (기본 eleven_v3)",
            "LIPSYNC_PROVIDER (기본 disabled)",
            "CORS_ORIGINS (추가 Origin. 필수 목록은 코드가 합친다)",
        ]
    )

    # ----- 13 -----
    pdf.h1("13. 운영과 관측")
    pdf.h2("13.1 헬스")
    pdf.table(
        ["엔드포인트", "의미", "실패 시"],
        [
            ["/healthz", "프로세스 up + env + version", "컨테이너/Caddy unhealthy"],
            ["/readyz", "repository.ping()", "503 degraded"],
            ["워커 HEALTHCHECK", "heartbeat 파일 60s", "compose restart"],
        ],
        [42, 70, 66],
    )
    pdf.h2("13.2 로그")
    pdf.p(
        "json-file, 파일당 10m, 3개. AccessLogMiddleware가 인증된 /v1 히트를 DB에 남긴다. "
        "워커는 잡 단위 logger. 파이프라인 단계 메시지는 jobs.progress 로 UI 라벨과 연결된다."
    )
    pdf.h2("13.3 배포 체크리스트")
    pdf.bullet(
        [
            "UI만 바꾸면 Pages 빌드(푸시 main 또는 wrangler). API 재시작 불필요.",
            "라우터·버전·CORS면 API 이미지 재빌드. healthz.version으로 확인.",
            "STT/번역/믹스/ffmpeg면 워커 이미지 재빌드. 진행 중 잡은 5분 grace.",
            "기존 프로젝트의 잘못된 세그먼트는 코드만으로 고쳐지지 않는다. 추출을 다시 돌려야 한다(더빙만 재실행하면 옛 자막을 유지).",
        ]
    )
    pdf.h2("13.4 용량 가드레일")
    pdf.p(
        "원본 최대 30분·500MB. 업로드 안전 상한 4GiB(스토리지). "
        "워커 잡 타임아웃 1시간. 스텝 재시도 기본 2회, 백오프 2초. 하트비트 20초."
    )

    # ----- 14 -----
    pdf.h1("14. 제약과 운영 주의")
    pdf.bullet(
        [
            "API workers=1. 수평 확장은 이 compose에 없다. 워커 동시성도 기본 1.",
            "Lightsail에 GPU 전제가 없다. Demucs는 CPU 휠.",
            "프로덕션 OpenAPI(/docs) 비활성.",
            "multipart 파트 16MiB 캡. 브라우저 두 번째 PUT 안정성.",
            "Windows 호스트 경로(D:\\ffmpeg)가 .env에 있어도 리눅스 컨테이너는 ffmpeg PATH로 정규화한다.",
            "IPv6 Docker DNS와 짧은 호스트명 api 조합을 다시 넣지 말 것.",
            "Caddy에 CORS를 중복 설정하지 말 것.",
            "로컬 step12(:8002)를 공인 엔드포인트로 쓰지 말 것.",
            "시크릿·.env·터널 로그·.wrangler/tmp 는 저장소에 올리지 않는다.",
        ]
    )
    pdf.h2("14.1 명시적으로 다루지 않는 것")
    pdf.p(
        "멀티 리전, GPU 워커 풀, CDN 앞단의 API 캐시, D1/KV 세션. "
        "현재 코드 경로에 없으면 이 아키텍처의 구성 요소가 아니다."
    )

    # ----- 15 -----
    pdf.h1("15. 부록 — 저장소 맵")
    pdf.table(
        ["경로", "내용"],
        [
            ["src/", "Next.js UI"],
            ["src/lib/api.ts, supabase.ts, mobile.ts", "오리진·세션·네이티브 분기"],
            ["next.config.ts, wrangler.jsonc", "static export, Pages 산출"],
            ["capacitor.config.ts", "네이티브 셸"],
            [".github/workflows/deploy-pages.yml", "GitHub Pages CI"],
            ["api/app/main.py", "FastAPI 팩토리"],
            ["api/app/config.py", "환경 변수"],
            ["api/app/auth.py", "JWT"],
            ["api/app/routers/", "HTTP 표면"],
            ["api/app/db/", "Repository"],
            ["api/app/storage/r2.py", "프리사인"],
            ["api/app/worker/", "러너·파이프라인·엔진"],
            ["api/Dockerfile", "api / worker 멀티 타깃"],
            ["infra/docker-compose.yml, Caddyfile", "Lightsail 스택"],
            ["api/app/local_step12.py", "로컬 전용. 비프로덕션"],
        ],
        [78, 100],
    )
    pdf.h2("15.1 개정")
    pdf.table(
        ["버전", "일자", "내용"],
        [
            ["1.0", DOC_DATE, "코드 3.0.12 기준 최초 아키텍처 정의서"],
        ],
        [28, 36, 114],
    )
    pdf.p(
        "다음 인수 문서: 「비즈니스 로직 및 기능 명세서」. "
        "본 문서는 그 기능이 어느 프로세스에서 실행되는지만 정의한다."
    )

    pdf.output(str(OUT))
    print(f"Wrote {OUT} ({OUT.stat().st_size} bytes, {pdf.page_no()} pages)")


if __name__ == "__main__":
    build()
