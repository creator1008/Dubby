# Dubby 모바일 출시 체크리스트

## 재현 가능한 프로젝트 생성

- 앱 ID: `com.dubby.app`, 앱 이름: `Dubby`, 웹 출력: `out`
- `npm install` 후 최초 1회 `npm run mobile:add:android` 및 macOS에서
  `npm run mobile:add:ios`를 실행한다.
- 이후 `npm run mobile:sync`로 Next 정적 export와 네이티브 플러그인을
  동기화한다. Android Studio/Xcode 실행은 각각 `mobile:open:*`을 사용한다.
- 네이티브 파일 선택은 WebView의 `<input type="file">` 시스템 picker를
  사용한다. 결과 다운로드는 앱 cache에 저장한 후 OS 공유/저장 sheet를 연다.

## RevenueCat / 스토어

- 각 스토어의 제품을 RevenueCat Offering에 package로 연결한다.
- 공개 SDK 키만 `NEXT_PUBLIC_REVENUECAT_ANDROID_API_KEY`,
  `NEXT_PUBLIC_REVENUECAT_IOS_API_KEY`에 배포 환경별로 설정한다.
- RevenueCat webhook URL은 `POST /v1/billing/revenuecat/webhook`이다.
  임의로 생성한 긴 Authorization header 값을 RevenueCat과 API의
  `REVENUECAT_WEBHOOK_AUTH_HEADER`에 동일하게 설정한다.
- 제품별 지급량은 `REVENUECAT_PRODUCT_CREDIT_MINUTES`, entitlement fallback은
  `REVENUECAT_ENTITLEMENT_CREDIT_MINUTES`로 설정한다. 저장소에는 실제 key나
  webhook 인증값을 넣지 않는다.
- Sandbox/TestFlight/Internal testing에서 구매, 갱신, 취소, 만료, 환불,
  복원, 계정 전환을 확인한다. 같은 webhook 재전송 시 크레딧이 중복 지급되지
  않고 환불 시 append-only 반대 원장 항목이 한 번만 생성되는지 확인한다.
- 웹에서는 기존 Stripe Checkout이 계속 사용되는지 확인한다.

## 개인정보와 음성 동의

- 업로드 전 사용자가 파일 및 음성을 처리·복제할 권리가 있음을 명시적으로
  확인한다. 앱은 동의 시각과 정책 버전을 기기 로컬에 저장하며 철회할 수 있다.
- 개인정보처리방침에 Supabase Auth, R2 저장, OpenAI/ElevenLabs/처리업체,
  RevenueCat/Apple/Google 결제 메타데이터, 보존 기간, 삭제 요청 절차를 적는다.
- 음성 복제 목적, 생성물 표시, 금지 사용, 신고/삭제 채널을 스토어 설명과
  앱 내 정책에 일관되게 표시한다.
- iOS App Privacy 및 Android Data safety 답변을 실제 수집/공유 동작과 맞춘다.
  추적을 사용하지 않으면 ATT를 요청하지 않는다.

## 제출 전

- 개인정보처리방침 URL, 지원 URL, 계정 삭제 경로, 이용약관을 공개한다.
- iOS In-App Purchase capability, agreements/tax/banking, product review metadata,
  restore 버튼을 확인한다.
- Play Billing product 활성화, license testers, Data safety, content rating,
  target API 요구사항을 확인한다.
- 아이콘/splash, 화면 회전, safe area, 키보드, 오프라인/느린 네트워크,
  큰 파일 업로드 중 중단과 재시도를 실제 기기에서 확인한다.
- `npm test`, `npm run lint`, `npm run build`, `npm run mobile:sync`,
  `cd api && python -m pytest`를 통과시킨다.
- iOS 빌드/서명은 macOS + Xcode가, Android 빌드는 Android SDK/JDK가 필요하다.
