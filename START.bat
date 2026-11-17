@echo off
cd /d "%~dp0"
set "PYTHONUTF8=1"
if exist "release-config.json" goto packaged
if exist "..\..\work\youtube-env\Scripts\python.exe" (
  "..\..\work\youtube-env\Scripts\python.exe" -m streamlit run app.py --server.address 127.0.0.1 --server.port 8517 --server.maxUploadSize 1024 --browser.gatherUsageStats false
  goto end
)
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if errorlevel 1 goto end
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto end
".venv\Scripts\python.exe" -m streamlit run app.py --server.address 127.0.0.1 --server.port 8517 --server.maxUploadSize 1024 --browser.gatherUsageStats false
goto end
:packaged
python launch.py %*
if not errorlevel 9009 goto end
py -3 launch.py %*
:end
pause
