@echo off
REM Cloudflare 터널 시작 스크립트

cd /d D:\Coding\Dubby

if not exist infra\cloudflared\config.yml (
    echo [ERROR] 터널 설정 파일이 없습니다.
    echo 먼저 설정하세요: bash scripts/setup-named-tunnel.sh
    pause
    exit /b 1
)

echo 로컬 origin 확인 중...
curl -s -m 3 http://127.0.0.1:8003/healthz >nul 2>&1
if errorlevel 1 (
    echo [WARNING] 포트 8003 홈 페치가 없습니다. YouTube 링크는 start-home-fetch.bat 이 필요합니다.
)
curl -s -m 3 http://127.0.0.1:8000/healthz >nul 2>&1
if errorlevel 1 (
    echo [WARNING] 포트 8000 API가 없습니다. 로컬 API를 쓸 때만 start-api.bat 이 필요합니다.
)

echo Cloudflare 터널을 시작합니다...
set CONFIG=infra\cloudflared\config.yml

REM cloudflared 찾기
set CF=
if exist "C:\Program Files\cloudflared\cloudflared.exe" set CF="C:\Program Files\cloudflared\cloudflared.exe"
if exist "%LOCALAPPDATA%\Temp\cloudflared.exe" set CF="%LOCALAPPDATA%\Temp\cloudflared.exe"
if exist "C:\Windows\System32\cloudflared.exe" set CF=cloudflared.exe
where cloudflared >nul 2>&1 && set CF=cloudflared

if "%CF%"=="" (
    echo [ERROR] cloudflared를 찾을 수 없습니다.
    echo 다운로드: https://github.com/cloudflare/cloudflared/releases/latest
    echo 또는 Git Bash에서: bash scripts/run-named-tunnel.sh
    pause
    exit /b 1
)

echo cloudflared 실행: %CF%
%CF% tunnel --config %CONFIG% --protocol http2 --edge-ip-version 4 run
