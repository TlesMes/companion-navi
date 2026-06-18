<#
.SYNOPSIS
    GPT-SoVITS Windows native CPU 추론용 .venv-voice 환경 셋업.

.DESCRIPTION
    실행 1회로 .venv-voice 전체를 구성한다.
    전제 조건:
      - Python 3.12 설치 (py -3.12 으로 호출 가능)
      - VS2019 BuildTools (C++ 워크로드) 설치
      - GPT-SoVITS repo: git clone --depth 1 https://github.com/RVC-Boss/GPT-SoVITS C:\gptsovits

    사용:
      # 프로젝트 루트에서 실행
      Set-ExecutionPolicy -Scope Process Bypass
      .\scripts\setup\setup_voice_env.ps1

      # 베이스 모델 다운로드 포함 (HF_TOKEN 권장, 없으면 익명 41kB/s)
      .\scripts\setup\setup_voice_env.ps1 -DownloadModels

.NOTES
    pyopenjtalk는 Windows prebuilt wheel이 없어 소스 컴파일 필요.
    VS2019 BuildTools + Windows SDK 10.0.19041.0 + cmake<4(venv에 설치됨)로 1회 빌드.
    이후 실행은 .pyd 바이너리만 import하므로 MSVC 불필요.
#>
param(
    [string]$GptSoVITSRepo = "C:\gptsovits",
    [string]$VenvDir      = ".venv-voice",
    [switch]$DownloadModels
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Proj = $PSScriptRoot | Split-Path | Split-Path  # scripts/setup → 프로젝트 루트

# ── 경로 상수 ────────────────────────────────────────────────────────────────
$VcVars  = "C:\Program Files (x86)\Microsoft Visual Studio\2019\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
$SdkBin  = "C:\Program Files (x86)\Windows Kits\10\bin\10.0.19041.0\x64"  # rc.exe 위치
$VenvAbs = Join-Path $Proj $VenvDir
$PyExe   = Join-Path $VenvAbs "Scripts\python.exe"
$PipExe  = Join-Path $VenvAbs "Scripts\pip.exe"

# ── 전제 조건 확인 ────────────────────────────────────────────────────────────
Write-Host "=== 전제 조건 확인 ===" -ForegroundColor Cyan

if (-not (Test-Path $GptSoVITSRepo)) {
    Write-Error "GPT-SoVITS repo 없음: $GptSoVITSRepo`n  git clone --depth 1 https://github.com/RVC-Boss/GPT-SoVITS $GptSoVITSRepo"
}
if (-not (Test-Path $VcVars)) {
    Write-Error "VS2019 BuildTools 없음: $VcVars`n  Visual Studio Installer → Build Tools 2019 → C++ 빌드 도구 설치"
}
if (-not (Test-Path $SdkBin\rc.exe)) {
    Write-Error "Windows SDK rc.exe 없음: $SdkBin`n  VS Installer → 개별 구성요소 → Windows 10 SDK (10.0.19041.0)"
}

# ── Step 1: venv 생성 ────────────────────────────────────────────────────────
Write-Host "`n=== Step 1: Python 3.12 venv 생성 ===" -ForegroundColor Cyan
if (-not (Test-Path $PyExe)) {
    & py -3.12 -m venv $VenvAbs
    Write-Host "venv 생성 완료: $VenvAbs"
} else {
    Write-Host "이미 존재 — 스킵: $VenvAbs"
}

# ── Step 2: cmake<4 설치 (pyopenjtalk 빌드 전에 필요) ────────────────────────
Write-Host "`n=== Step 2: cmake<4 설치 ===" -ForegroundColor Cyan
& $PipExe install "cmake<4" --quiet

# ── Step 3: pyopenjtalk 소스 빌드 (vcvars64 환경 필요) ───────────────────────
Write-Host "`n=== Step 3: pyopenjtalk 소스 빌드 (VS2019 + cmake) ===" -ForegroundColor Cyan
Write-Host "  이 단계는 처음 실행 시 ~5분 소요됩니다."

$VenvScripts = Join-Path $VenvAbs "Scripts"

# vcvars64를 호출한 뒤 같은 cmd 세션에서 pip 실행 (PowerShell에서 환경 변수 유지 불가)
$BuildCmd = @"
"$VcVars" && ^
set PATH=$VenvScripts;$SdkBin;%PATH% && ^
set DISTUTILS_USE_SDK=1 && ^
set MSSdk=1 && ^
"$PyExe" -m pip install "pyopenjtalk<0.4" --no-binary=pyopenjtalk
"@

cmd /c $BuildCmd
if ($LASTEXITCODE -ne 0) {
    Write-Error "pyopenjtalk 빌드 실패 (exit $LASTEXITCODE). 위 오류 메시지를 확인하세요."
}
Write-Host "pyopenjtalk 빌드 완료."

# ── Step 4: torch CPU 설치 ───────────────────────────────────────────────────
Write-Host "`n=== Step 4: PyTorch CPU 설치 ===" -ForegroundColor Cyan
Write-Host "  torch 2.12+cpu + torchaudio 2.11+cpu (~1.5GB)"
& $PipExe install torch torchaudio --index-url https://download.pytorch.org/whl/cpu --quiet

# ── Step 5: GPT-SoVITS Windows CPU requirements ──────────────────────────────
Write-Host "`n=== Step 5: GPT-SoVITS 의존성 설치 ===" -ForegroundColor Cyan
& $PipExe install -r (Join-Path $Proj "requirements.win-cpu.txt") --quiet

# ── Step 6: sounddevice + navi ────────────────────────────────────────────────
Write-Host "`n=== Step 6: sounddevice + navi 설치 ===" -ForegroundColor Cyan
& $PipExe install sounddevice --quiet
& $PipExe install -e (Join-Path $Proj ".") --quiet

# ── Step 7: fast_langdetect 캐시 디렉토리 생성 ───────────────────────────────
Write-Host "`n=== Step 7: fast_langdetect 캐시 디렉토리 생성 ===" -ForegroundColor Cyan
$LangDetectDir = Join-Path $GptSoVITSRepo "GPT_SoVITS\pretrained_models\fast_langdetect"
New-Item -ItemType Directory -Force -Path $LangDetectDir | Out-Null
Write-Host "  생성: $LangDetectDir"

# ── Step 8: pyopenjtalk mecab 사전을 ASCII 경로로 복사 ───────────────────────
# venv가 한글 경로(예: '반려 ai 어플리케이션')면 mecab C++가 사전 경로를 ANSI로
# 해석해 못 연다 → 일본어 G2P가 'Failed to initialize Mecab'으로 죽는다. 단축경로(8.3)도
# cp949 유효 한글이 남아 무력. 사전을 ASCII 경로(repo 옆)로 복사하고 어댑터가
# OPEN_JTALK_DICT_DIR로 가리킨다(navi/mouth/gptsovits.py). 사전은 패키지에 없고 첫
# 사용 시 다운로드되므로, 다운로드를 먼저 트리거(mecab init 실패는 무시 — tar 추출까지는 됨)한다.
Write-Host "`n=== Step 8: pyopenjtalk mecab 사전 ASCII 경로 복사 ===" -ForegroundColor Cyan
$JtalkSrc = Join-Path $VenvAbs "Lib\site-packages\pyopenjtalk\open_jtalk_dic_utf_8-1.11"
$JtalkDst = Join-Path $GptSoVITSRepo "open_jtalk_dic_utf_8-1.11"
if (Test-Path $JtalkDst) {
    Write-Host "  이미 존재 — 스킵: $JtalkDst"
} else {
    if (-not (Test-Path $JtalkSrc)) {
        Write-Host "  사전 다운로드 트리거 (mecab init 실패는 무시)..."
        $DicTrigger = @"
import pyopenjtalk
try:
    pyopenjtalk.run_frontend(chr(0x3042))  # 'あ' — ASCII 외 문자 회피
except Exception:
    pass
"@
        & $PyExe -c $DicTrigger
    }
    if (Test-Path $JtalkSrc) {
        Copy-Item -Recurse $JtalkSrc $JtalkDst
        Write-Host "  복사 완료: $JtalkDst"
    } else {
        Write-Warning "  pyopenjtalk 사전을 찾지 못함 — 첫 일본어 합성 시 자동 다운로드 후"
        Write-Warning "  '$JtalkSrc' → '$JtalkDst' 수동 복사 필요"
    }
}

# ── Step 9: 베이스 모델 다운로드 (선택) ─────────────────────────────────────
if ($DownloadModels) {
    Write-Host "`n=== Step 9: 베이스 모델 다운로드 (cnhubert + roberta, ~820MB) ===" -ForegroundColor Cyan
    Write-Host "  HF_TOKEN 환경변수 있으면 인증 다운로드 (없으면 익명 ~41kB/s로 느림)"

    $HfToken = if ($env:HF_TOKEN) { $env:HF_TOKEN } else { $null }

    # .env에서 HF_TOKEN 읽기 시도
    if (-not $HfToken) {
        $EnvFile = Join-Path $Proj ".env"
        if (Test-Path $EnvFile) {
            $HfToken = (Get-Content $EnvFile | Where-Object { $_ -match "^HF_TOKEN=" }) -replace "^HF_TOKEN=", ""
        }
    }

    $PretrainedDir = Join-Path $GptSoVITSRepo "GPT_SoVITS\pretrained_models"
    $DownloadScript = @"
from huggingface_hub import snapshot_download
import os
token = r'$HfToken' or None
snapshot_download(
    'lj1995/GPT-SoVITS',
    allow_patterns=['chinese-hubert-base/*', 'chinese-roberta-wwm-ext-large/*'],
    local_dir=r'$PretrainedDir',
    token=token if token else None,
)
print('다운로드 완료')
"@
    & $PyExe -c $DownloadScript
}

# ── 완료 ────────────────────────────────────────────────────────────────────
Write-Host "`n=== 셋업 완료 ===" -ForegroundColor Green
Write-Host ""
Write-Host "다음 단계:"
Write-Host "  1. 첫 추론 시 lid.176.bin(125MB)이 자동 다운로드됩니다 (~1회)."
Write-Host "     (open_jtalk 사전은 Step 8에서 ASCII 경로로 복사됨 — 일본어 합성용)"
Write-Host "  2. arisu ckpt 2개가 voice_ref/ 에 있는지 확인:"
Write-Host "       voice_ref\arisu-e15.ckpt      (GPT)"
Write-Host "       voice_ref\arisu_e8_s352.pth   (SoVITS)"
Write-Host "  3. 검증:"
Write-Host "     $PyExe scripts\try\try_clone.py --model gptsovits \"
Write-Host "       --sovits-repo $GptSoVITSRepo \"
Write-Host "       --reference dataset\arisu\wavs\Arisu_LogIn_1.wav \"
Write-Host "       --ref-text 'ようこそ先生。アリスは先生を待っていました。' \"
Write-Host "       --text 'アリスはメイド勇者になります！' --device cpu"
