#!/bin/sh
set -u

PYTHON_BIN=""

if [ -n "${YMB_WORKBUDDY_PYTHON:-}" ] && [ -x "${YMB_WORKBUDDY_PYTHON}" ]; then
    PYTHON_BIN=${YMB_WORKBUDDY_PYTHON}
elif [ -n "${HOME:-}" ] && [ -x "${HOME}/.workbuddy/binaries/python/envs/default/bin/python" ]; then
    PYTHON_BIN=${HOME}/.workbuddy/binaries/python/envs/default/bin/python
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN=$(command -v python3)
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN=$(command -v python)
fi

if [ -z "${PYTHON_BIN}" ]; then
    printf '%s\n' '{"run_id":"","status":"ERROR","next_action":"REPORT_ERROR","reason_code":"PYTHON_RUNTIME_NOT_FOUND","artifact_refs":[],"context_ref":"","message":"未找到可用 Python 运行时；请设置 YMB_WORKBUDDY_PYTHON","contract_version":1}'
    exit 1
fi

if [ "$#" -eq 0 ]; then
    printf '%s\n' '{"run_id":"","status":"ERROR","next_action":"REPORT_ERROR","reason_code":"PYTHON_ENTRYPOINT_REQUIRED","artifact_refs":[],"context_ref":"","message":"缺少 Python 正式入口脚本","contract_version":1}'
    exit 1
fi

exec "${PYTHON_BIN}" "$@"
