@echo off
setlocal
rem ============================================================
rem  멍멍이 영어 놀이터 실행기 (kids-english)
rem  - 더블클릭하면 서버를 켜고 Edge로 놀이터를 연다.
rem  - 서버가 이미 켜져 있으면 브라우저만 연다.
rem  - 이 창을 닫거나 Ctrl+C 를 누르면 서버가 꺼진다.
rem  경로는 %~dp0 (이 파일이 있는 폴더)만 쓴다: 한글 경로 깨짐 방지
rem ============================================================
cd /d "%~dp0"
title 멍멍이 영어 놀이터 (kids-english)

set "URL=http://127.0.0.1:8771/"
set "HEALTH=http://127.0.0.1:8771/api/health"

rem 1) 이미 켜져 있으면 브라우저만 연다
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 '%HEALTH%' | Out-Null; exit 0 } catch { exit 1 }"
if %errorlevel%==0 (
    echo 서버가 이미 켜져 있습니다. 놀이터만 엽니다.
    start "" msedge "%URL%"
    timeout /t 3 >nul
    exit /b 0
)

rem 2) 준비물 확인
if not exist ".venv\Scripts\python.exe" (
    echo [오류] .venv 가 없습니다. Claude에게 'kids-english 환경 다시 만들어 줘'라고 부탁하세요.
    pause
    exit /b 1
)
if not exist ".env" (
    echo [안내] .env 토큰이 없어서 자유 대화는 준비된 대사로만 나옵니다. 즉시 반응 명령은 그대로 됩니다.
)

rem 3) 서버가 뜨면 Edge를 연다 (뒤에서 최대 20초 기다림)
start "" /min powershell -NoProfile -WindowStyle Hidden -Command "for ($i = 0; $i -lt 40; $i++) { try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 '%HEALTH%' | Out-Null; Start-Process msedge '%URL%'; break } catch { Start-Sleep -Milliseconds 500 } }"

echo.
echo  멍멍이 영어 놀이터를 켜는 중입니다...
echo  - 놀이터: %URL%
echo  - 부모용 디버그: %URL%?debug=1
echo  - 구조 설명 문서: %URL%docs/
echo  끌 때는 이 창을 닫으세요.
echo.
".venv\Scripts\python.exe" run.py

echo.
echo 서버가 멈췄습니다. 위에 오류가 있으면 Claude에게 보여 주세요.
pause
