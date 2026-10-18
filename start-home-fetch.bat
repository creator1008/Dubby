@echo off
REM Home-PC YouTube fetch for Lightsail (residential IP).
REM Keep this window open while using YouTube links on dubbyai.com.

cd /d D:\Coding\Dubby

if not exist api\.venv (
    echo [ERROR] 가상환경이 없습니다. 먼저 설정하세요:
    echo   python -m venv api\.venv
    echo   api\.venv\Scripts\activate
    echo   pip install -r api\requirements-dev.txt
    pause
    exit /b 1
)

echo Checking YTDLP_HOME_FETCH_TOKEN in api/.env ...
api\.venv\Scripts\python.exe scripts\ensure-home-fetch-token.py
echo.
echo Home fetch listening on http://127.0.0.1:8003
echo Also start the named tunnel (start-tunnel.bat) so fetch.dubbyai.com reaches this port.
echo Do NOT set YTDLP_HOME_FETCH_URL on this PC.
echo.
cd /d D:\Coding\Dubby\api
.venv\Scripts\python.exe -m uvicorn app.home_fetch:app --host 127.0.0.1 --port 8003
