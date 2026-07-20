# Dubby

Next.js 정적 프런트엔드, FastAPI, Supabase Postgres/Auth, Cloudflare R2,
Stripe, 비동기 미디어 worker로 구성된 더빙 서비스입니다.

## 로컬 설정

### 현재 단계: 실제 오디오·자막 추출 검증

백엔드 계정 없이 요구사항 1–2단계만 로컬에서 실행합니다. 두 터미널을
사용합니다.

```bash
# 터미널 1
cd api
python -m pip install -e ".[local]"
uvicorn app.local_step12:app --reload --port 8002

# 터미널 2 (저장소 루트)
npm install
npm run dev
```

`http://localhost:3000/app/new/`에서 원어를 정확히 선택하고 영상을
업로드합니다. 서버는 `LOCAL_WHISPER_MODEL=medium`(기본값)을 사용하며,
더 높은 정확도가 필요하면 `large-v3-turbo`로 바꿀 수 있습니다.
첫 실행에는 모델 다운로드 시간이 필요합니다. 결과는
`.local-data/step12/<run-id>/`에 다음과 같이 남습니다.

- `original_audio.wav`: 48kHz stereo 원본 추출 오디오
- `asr_audio.wav`: 음성인식용 16kHz mono 오디오
- `speech/*.wav`: 타임스탬프별 음성 클립
- `manifest.json`: 타임스탬프·텍스트·클립 경로 쌍

웹 화면에서 전체 추출 오디오와 각 음성 클립을 직접 재생해 자막과
타임스탬프를 검증할 수 있습니다. 3단계 이후 기능은 이 검증이 끝날 때까지
로컬 데모에서 비활성화됩니다.

### 전체 SaaS 스택

```bash
cp .env.example .env.local
cp api/.env.example api/.env
npm install
python -m pip install -r api/requirements-dev.txt
```

Supabase migration을 파일명 순서대로 적용한 뒤 API와 worker를 실행합니다.

```bash
cd api
uvicorn app.main:app --reload
python -m app.worker.runner
```

다른 터미널에서 `npm run dev`를 실행합니다. 프런트엔드는 서버 기능을
사용하지 않으며 `output: "export"`를 유지합니다. 실제 프로젝트 UUID는
정적으로 생성된 `/app/projects/_/` 셸의 `?id=` 값으로 전달됩니다.

## 환경변수

Cloudflare Pages:

- `NEXT_PUBLIC_API_ORIGIN`
- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`

API의 전체 목록과 안전한 예시는 `api/.env.example`에 있습니다. 특히
Stripe secret/webhook secret, 구독/크레딧 팩 Price ID, Checkout
success/cancel URL을 설정해야 합니다. 실제 키는 저장소에 커밋하지 않습니다.

### 소셜 로그인과 관리자

Supabase Dashboard의 Authentication > Providers에서 Google, Facebook,
Kakao를 활성화하고 각 공급자 개발자 콘솔의 callback URL에
`https://<project-ref>.supabase.co/auth/v1/callback`을 등록합니다.
Supabase URL Configuration의 허용 redirect URL에는 로컬 개발용
`http://localhost:3000/auth/callback/`과 운영 도메인의 `/auth/callback/`을
추가합니다.

관리자 API는 사용자가 임의로 수정할 수 없는 Supabase
`app_metadata.role = "admin"` claim만 신뢰합니다. 첫 관리자는 Supabase
Dashboard 또는 service-role을 사용하는 안전한 서버 작업으로 지정한 뒤
다시 로그인해 JWT를 갱신합니다. 브라우저 코드나 `user_metadata`에 관리자
권한을 저장하면 안 됩니다. 관리자 화면은 `/admin`입니다.

최초 관리자는 migration
`20260719165000_bootstrap_first_admin.sql`에서
`passionmasters@gmail.com`으로 지정됩니다. 이미 migration을 적용한
환경에서는 Supabase SQL Editor에서 다음 구문을 한 번 실행한 뒤 해당
사용자가 로그아웃하고 다시 로그인해야 합니다.

```sql
update auth.users
set raw_app_meta_data =
  coalesce(raw_app_meta_data, '{}'::jsonb) || '{"role":"admin"}'::jsonb
where lower(email) = 'passionmasters@gmail.com';
```

Stripe webhook URL은 `POST /v1/billing/webhook`이며 다음 이벤트를
전송하도록 구성합니다.

- `checkout.session.completed`
- `invoice.paid`
- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`

Webhook은 raw body 서명을 검증하고 `stripe_events`에서 이벤트 ID를
원자적으로 중복 제거합니다. 일회성 결제는 payment intent, 구독 갱신은
invoice ID를 ledger idempotency key로 사용해 크레딧을 한 번만 지급합니다.

모바일 앱은 Capacitor에서 RevenueCat Offering을 사용하고, 같은 Supabase
사용자 UUID를 RevenueCat App User ID로 사용합니다. 웹은 Stripe Checkout을
그대로 사용합니다. 설정, native sync, 개인정보/음성 동의 및 스토어 제출
체크리스트는 [`docs/MOBILE_RELEASE_CHECKLIST.md`](docs/MOBILE_RELEASE_CHECKLIST.md)에
정리되어 있습니다.

R2 bucket CORS에는 Pages origin의 `PUT`, `GET`을 허용하고 multipart
업로드 완료에 필요한 `ETag` 응답 헤더를 expose해야 합니다. 다운로드 URL은
API에서 소유권과 완료 상태를 확인한 뒤 기본 5분 동안만 서명됩니다.

## 크레딧 정책

`credit_ledger`는 update/delete가 금지된 append-only 원장입니다. dub job
enqueue와 예상 분 크레딧 차감은 하나의 DB transaction/RPC로 처리됩니다.
잔액 부족은 HTTP 402이며, 실패·취소·worker timeout은 최초 차감액 전부를
idempotent refund합니다. `DUB_COGS_MINUTES_MULTIPLIER`로 예상 COGS 비율을
조정할 수 있습니다.

## Phase 2 품질 및 프리미엄

프로젝트는 `neutral`, `warm`, `energetic`, `serious` 감정톤과 선택적
다화자 감지를 지원합니다. 다화자는 기본적으로 꺼져 있으며
`DIARIZATION_PROVIDER=pyannote`와 토큰을 설정해야 실제 모델을 사용합니다.
겹친 화자는 안전하게 기본 음성으로 처리되고 프로젝트의
`quality_warnings`에 기록됩니다. 번역은 세그먼트 목표 시간을 전달하고,
실제 TTS 길이를 측정한 뒤 한 번 압축/확장하며, 허용 속도 범위를 넘으면
겹침 방지를 위해 슬롯 끝에서 자르고 명시적인 품질 경고를 남깁니다.

프리미엄 립싱크는 별도 `lipsync` job으로 크레딧을 원자적으로 차감합니다.
`LIPSYNC_PROVIDER=sync`와 `SYNC_API_KEY`를 설정하면 Sync Labs job을
idempotency key로 생성하고 제한 시간 동안 polling한 뒤 결과를 R2에
저장합니다. 미설정 상태는 HTTP 503 `feature_unavailable`이며,
`mock` 모드는 외부 secret 없이 E2E 테스트에 사용됩니다.

## 검증

```bash
cd api && python -m pytest
npm test
npm run lint
npm run build
```
