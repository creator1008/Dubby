@echo off
REM Local YouTube save for Dubby "PC에 저장". Binds 127.0.0.1:8003 only.

cd /d D:\Coding\Dubby

if not exist api\.venv (
    echo [ERROR] 가상환경이 없습니다.
    echo   python -m venv api\.venv
    echo   api\.venv\Scripts\activate
    echo   pip install -r api\requirements-dev.txt
    pause
    exit /b 1
)

echo YouTube PC 저장: http://127.0.0.1:8003
echo Dubby에서 유튜브 링크를 넣은 뒤 [PC에 저장]을 누르세요.
echo.
cd /d D:\Coding\Dubby\api
.venv\Scripts\python.exe -m uvicorn app.local_youtube_save:app --host 127.0.0.1 --port 8003
