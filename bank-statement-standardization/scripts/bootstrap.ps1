param(
    [string]$PythonExe = "",
    [string]$InstallRoot = "",
    [string]$VenvRoot = ""
)

$ErrorActionPreference = "Stop"
$SkillRoot = Split-Path -Parent $PSScriptRoot
if (-not $InstallRoot) {
    $InstallRoot = Join-Path $SkillRoot ".runtime\python-3.8.6"
}
if (-not $VenvRoot) {
    $VenvRoot = Join-Path $SkillRoot ".runtime\venv-3.8.6"
}
$Stamp = Get-Date -Format "yyyyMMddTHHmmss"
$LogRoot = Join-Path $SkillRoot "bootstrap-logs\$Stamp"
$LogPath = Join-Path $LogRoot "bootstrap.log"
$ErrorZip = Join-Path $LogRoot "${Stamp}__BOOTSTRAP_ERROR.zip"
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
Start-Transcript -Path $LogPath -Force | Out-Null

function Test-Python386([string]$Candidate) {
    if (-not $Candidate -or -not (Test-Path -LiteralPath $Candidate)) {
        return $false
    }
    try {
        $Version = & $Candidate -c "import platform; print(platform.python_version())"
        return $LASTEXITCODE -eq 0 -and $Version.Trim() -eq "3.8.6"
    } catch {
        return $false
    }
}

function Find-Python386 {
    $Candidates = @(
        $PythonExe,
        (Join-Path $InstallRoot "python.exe"),
        "D:\Program Files\python3.8.6\python.exe"
    )
    $PathPython = Get-Command python -ErrorAction SilentlyContinue
    if ($PathPython) {
        $Candidates += $PathPython.Source
    }
    foreach ($Candidate in $Candidates | Select-Object -Unique) {
        if (Test-Python386 $Candidate) {
            return $Candidate
        }
    }
    return $null
}

function Write-ErrorBundle([string]$Message) {
    Write-Host "[ERROR][BOOTSTRAP_ABORTED] $Message"
    Stop-Transcript | Out-Null
    Compress-Archive -Path (Join-Path $LogRoot "*") -DestinationPath $ErrorZip -Force
    Write-Host "[ERROR][BOOTSTRAP_BUNDLE] $ErrorZip"
}

try {
    $Python = Find-Python386
    if (-not $Python) {
        Write-Host "[INFO][PYTHON_INSTALL] Installing Python 3.8.6 into $InstallRoot"
        $Installer = Join-Path $LogRoot "python-3.8.6-amd64.exe"
        Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.8.6/python-3.8.6-amd64.exe" -OutFile $Installer
        $Args = @(
            "/quiet",
            "InstallAllUsers=0",
            "Include_launcher=0",
            "Include_test=0",
            "Include_pip=1",
            "PrependPath=0",
            "TargetDir=$InstallRoot"
        )
        $Process = Start-Process -FilePath $Installer -ArgumentList $Args -Wait -PassThru
        if ($Process.ExitCode -ne 0) {
            throw "Python 3.8.6 installer failed with exit code $($Process.ExitCode)"
        }
        $Python = Join-Path $InstallRoot "python.exe"
    }
    if (-not (Test-Python386 $Python)) {
        throw "Python 3.8.6 validation failed: $Python"
    }
    Write-Host "[INFO][PYTHON_READY] $Python"
    $VenvPython = Join-Path $VenvRoot "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        Write-Host "[INFO][VENV_CREATE] $VenvRoot"
        & $Python -m venv $VenvRoot
        if ($LASTEXITCODE -ne 0) {
            throw "python -m venv failed"
        }
    }
    if (-not (Test-Python386 $VenvPython)) {
        throw "Private venv Python 3.8.6 validation failed: $VenvPython"
    }
    & $VenvPython -m pip install -r (Join-Path $SkillRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "pip install -r requirements.txt failed"
    }
    Write-Host "[INFO][REQUIREMENTS_READY] requirements.txt installed"
    Stop-Transcript | Out-Null
    Write-Host "[OK][BOOTSTRAP_COMPLETE] $VenvPython"
    exit 0
} catch {
    Write-ErrorBundle $_.Exception.Message
    exit 1
}
