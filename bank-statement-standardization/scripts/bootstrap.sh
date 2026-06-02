#!/usr/bin/env bash
set -euo pipefail

SKILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT="${PYTHON386_ROOT:-"$SKILL_ROOT/.runtime/python-3.8.6"}"
VENV_ROOT="${PYTHON386_VENV:-"$SKILL_ROOT/.runtime/venv-3.8.6"}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_ROOT="$SKILL_ROOT/bootstrap-logs/$STAMP"
LOG_PATH="$LOG_ROOT/bootstrap.log"
ERROR_ZIP="$LOG_ROOT/${STAMP}__BOOTSTRAP_ERROR.zip"
mkdir -p "$LOG_ROOT"
exec > >(tee -a "$LOG_PATH") 2>&1

bundle_error() {
  local message="$1"
  echo "[ERROR][BOOTSTRAP_ABORTED] $message"
  if command -v zip >/dev/null 2>&1; then
    (cd "$LOG_ROOT" && zip -qr "$ERROR_ZIP" . -x "$(basename "$ERROR_ZIP")")
    echo "[ERROR][BOOTSTRAP_BUNDLE] $ERROR_ZIP"
  else
    tar -czf "${ERROR_ZIP%.zip}.tar.gz" -C "$LOG_ROOT" .
    echo "[ERROR][BOOTSTRAP_BUNDLE] ${ERROR_ZIP%.zip}.tar.gz"
  fi
}
trap 'bundle_error "bootstrap failed at line $LINENO"' ERR

is_python386() {
  [[ -x "$1" ]] && [[ "$("$1" -c 'import platform; print(platform.python_version())')" == "3.8.6" ]]
}

PYTHON=""
for candidate in "${PYTHON386_EXE:-}" "$INSTALL_ROOT/bin/python3.8" "$(command -v python3.8 || true)" "$(command -v python3 || true)" "$(command -v python || true)"; do
  if [[ -n "$candidate" ]] && is_python386 "$candidate"; then
    PYTHON="$candidate"
    break
  fi
done

if [[ -z "$PYTHON" ]]; then
  echo "[INFO][PYTHON_INSTALL] Building Python 3.8.6 into $INSTALL_ROOT"
  ARCHIVE="$LOG_ROOT/Python-3.8.6.tgz"
  SOURCE="$LOG_ROOT/Python-3.8.6"
  curl -fL "https://www.python.org/ftp/python/3.8.6/Python-3.8.6.tgz" -o "$ARCHIVE"
  tar -xzf "$ARCHIVE" -C "$LOG_ROOT"
  (
    cd "$SOURCE"
    ./configure --prefix="$INSTALL_ROOT" --with-ensurepip=install
    make -j"${JOBS:-2}"
    make install
  )
  PYTHON="$INSTALL_ROOT/bin/python3.8"
fi

if ! is_python386 "$PYTHON"; then
  echo "[ERROR][PYTHON_VERSION] Python 3.8.6 validation failed: $PYTHON"
  exit 1
fi

echo "[INFO][PYTHON_READY] $PYTHON"
VENV_PYTHON="$VENV_ROOT/bin/python3.8"
if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "[INFO][VENV_CREATE] $VENV_ROOT"
  "$PYTHON" -m venv "$VENV_ROOT"
fi
if ! is_python386 "$VENV_PYTHON"; then
  echo "[ERROR][PYTHON_VERSION] Private venv Python 3.8.6 validation failed: $VENV_PYTHON"
  exit 1
fi
"$VENV_PYTHON" -m pip install -r "$SKILL_ROOT/requirements.txt"
echo "[OK][BOOTSTRAP_COMPLETE] $VENV_PYTHON"
