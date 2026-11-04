@echo off
rem Unified config-driven E2E entry (Windows convenience wrapper).
rem Usage: run.bat --list  |  run.bat --target spa_demo  |  run.bat
setlocal
set "RUN_DIR=%~dp0"
if "%RUN_DIR:~-1%"=="\" set "RUN_DIR=%RUN_DIR:~0,-1%"
set "VENV_PY=%RUN_DIR%\target_systems\hospital_sut\.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
  echo [run] venv python missing: %VENV_PY%
  exit /b 1
)
"%VENV_PY%" "%RUN_DIR%\run.py" %*
exit /b %ERRORLEVEL%