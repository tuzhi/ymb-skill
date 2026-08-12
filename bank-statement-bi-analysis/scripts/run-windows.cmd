@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "YMB_PYTHON_BIN="
set "YMB_USE_PY_LAUNCHER="

if defined YMB_WORKBUDDY_PYTHON if exist "%YMB_WORKBUDDY_PYTHON%" set "YMB_PYTHON_BIN=%YMB_WORKBUDDY_PYTHON%"
if not defined YMB_PYTHON_BIN if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\python.exe" set "YMB_PYTHON_BIN=%USERPROFILE%\.workbuddy\binaries\python\envs\python.exe"
if not defined YMB_PYTHON_BIN if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\default\python.exe" set "YMB_PYTHON_BIN=%USERPROFILE%\.workbuddy\binaries\python\envs\default\python.exe"
if not defined YMB_PYTHON_BIN if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe" set "YMB_PYTHON_BIN=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not defined YMB_PYTHON_BIN where python >nul 2>nul && set "YMB_PYTHON_BIN=python"
if not defined YMB_PYTHON_BIN where py >nul 2>nul && set "YMB_USE_PY_LAUNCHER=1"

if not defined YMB_PYTHON_BIN if not defined YMB_USE_PY_LAUNCHER goto runtime_missing
if "%~1"=="" goto entrypoint_missing

if defined YMB_USE_PY_LAUNCHER (
    py -3.11 %*
) else (
    "%YMB_PYTHON_BIN%" %*
)
exit /b %ERRORLEVEL%

:entrypoint_missing
echo {"bi_run_id":"","status":"ERROR","next_action":"REPORT_ERROR","reason_code":"PYTHON_ENTRYPOINT_REQUIRED","artifact_refs":[],"message":"Python entrypoint script is required","contract_version":1}
exit /b 1

:runtime_missing
echo {"bi_run_id":"","status":"ERROR","next_action":"REPORT_ERROR","reason_code":"PYTHON_RUNTIME_NOT_FOUND","artifact_refs":[],"message":"Python runtime not found; set YMB_WORKBUDDY_PYTHON","contract_version":1}
exit /b 1
