<#
.SYNOPSIS
    나비 데몬·GUI 표준 실행 — venv 선택·필수 인자·작업 디렉토리를 한 곳에서 소유한다.

.DESCRIPTION
    실행 방법이 문서 산문에 흩어져 있으면 매번 조립해야 하고 틀리기 쉽다.
    이 스크립트가 그 단일 출처다 — 사람도, GUI 대기 화면도 여기를 부른다.

    무인자 실행 = 최종 제품 형태(음성 + 웨이크워드 + Claude brain).

    사용:
      # 음성 데몬 (기본)
      .\scripts\run_navi.ps1

      # 텍스트 전용 / GUI / 종료
      .\scripts\run_navi.ps1 -Mode text
      .\scripts\run_navi.ps1 -Mode gui
      .\scripts\run_navi.ps1 -Mode stop

      # 실구동 테스트 — 본 기억(navi.db)과 격리
      .\scripts\run_navi.ps1 -Db logs\test.db

      # 다른 페르소나로 부팅 — 이름만 (엔진은 부팅 카드가 결정한다)
      .\scripts\run_navi.ps1 -Persona aris

      # 벤더 비교 / 모델링 안 한 인자
      .\scripts\run_navi.ps1 -Brain gemini -ExtraArgs '-vv'

.NOTES
    작업 디렉토리를 프로젝트 루트로 고정하는 것이 이 스크립트의 핵심 역할 중 하나다.
    데몬은 임포트 시점에 logs/navi.pid·navi.stop을 resolve하고 load_config는 Path.cwd()로
    config.yaml·.env를 읽는다 — 다른 디렉토리에서 띄우면 pid 파일이 딴 곳에 생겨
    단일 인스턴스 가드와 stop 명령이 조용히 무력해진다.

    --mouth를 넘기지 않는 것이 정상이다. TTS 벤더는 부팅 카드의 voice 번들이 결정한다
    (2026.07.10 결정, PR #24). -Mouth는 텍스트 스모크에 fake를 강제할 때만 쓴다.
#>
param(
    [ValidateSet("voice", "text", "gui", "stop")]
    [string]$Mode = "voice",

    [ValidateSet("anthropic", "gemini", "echo", "")]
    [string]$Brain = "anthropic",   # 최종 형태. config는 D1 보류라 gemini 그대로 — 여기서만 덮는다

    [ValidateSet("fake", "supertonic", "gptsovits", "")]
    [string]$Mouth = "",            # 빈 값 = 카드 번들이 결정 (정상 경로)

    [string]$Persona = "",          # 이름만 (personas/<이름>.yaml). 빈 값 = config의 card_path
    [string]$Db = "",               # 실구동 테스트 격리용
    [double]$VadThreshold = 50,     # 이 머신 마이크 실측 — 기본 150은 발화가 STT로 안 넘어감
                                    # (config의 ear.wakeword.openwakeword.vad_threshold와 다른 손잡이)
    [int]$Mic = -1,                 # -1 = 기본 입력 장치
    [switch]$NoWakeWord,            # voice 모드는 웨이크워드 기본 ON
    [string[]]$ExtraArgs = @()      # 모델링 안 한 인자 탈출구 (-vv 등)
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Proj = $PSScriptRoot | Split-Path   # scripts → 프로젝트 루트

# ── venv 선택: 음성 스택은 별도 3.12 venv, 나머지는 기본 venv ──────────────────
# stop은 navi 코어만 있으면 되므로 있는 venv 아무거나 쓴다 — 음성 venv만 구성한
# 사용자가 "내릴 venv가 없다"는 이유로 자기 데몬을 못 내리면 곤란하다.
$VenvDir =
    if ($Mode -eq "voice") { ".venv-voice" }
    elseif ($Mode -eq "stop" -and -not (Test-Path (Join-Path $Proj ".venv\Scripts\python.exe"))) {
        ".venv-voice"
    } else { ".venv" }
$VenvAbs = Join-Path $Proj $VenvDir
$PyExe   = Join-Path $VenvAbs "Scripts\python.exe"

# ── 전제 조건 (실패 메시지에 해결 명령을 함께) ────────────────────────────────
if (-not (Test-Path $PyExe)) {
    if ($Mode -eq "voice") {
        Write-Error "음성 venv 없음: $PyExe`n  .\scripts\setup\setup_voice_env.ps1"
    } else {
        Write-Error "기본 venv 없음: $PyExe`n  py -m venv $VenvDir; .\$VenvDir\Scripts\pip install -e ."
    }
}
if (-not (Test-Path (Join-Path $Proj "config.yaml"))) {
    Write-Error "config.yaml 없음: $Proj`n  프로젝트 루트에서 실행하거나 리포를 확인하세요"
}
# brain을 쓰는 모드에서만 키를 본다 — gui는 컨트롤 플레인에만 붙고 brain을 안 쓴다.
if ($Mode -notin @("stop", "gui") -and $Brain -eq "anthropic") {
    # 키의 존재만 확인한다 — 값은 읽지도 출력하지도 않는다.
    # 데몬은 load_dotenv 후 os.getenv라 .env 밖(프로세스 환경변수)의 키도 쓴다 —
    # 검사가 데몬보다 엄격하면 되는 실행을 막게 된다.
    $envFile = Join-Path $Proj ".env"
    $hasKey = [bool]$env:ANTHROPIC_API_KEY -or
              ((Test-Path $envFile) -and
               ((Get-Content $envFile) -match '^\s*ANTHROPIC_API_KEY\s*=\s*\S'))
    if (-not $hasKey) {
        Write-Error "ANTHROPIC_API_KEY 없음(.env·환경변수 모두)`n  .env.example을 .env로 복사해 키를 채우거나 -Brain gemini"
    }
}
# GPT-SoVITS repo는 일부러 검사하지 않는다 — 어느 벤더가 선택될지는 카드가 정하므로
# 여기서 추측하면 거짓 실패가 난다. 데몬이 스스로 말하게 둔다.
# logs/도 불요 — daemon.acquire_pidfile이 mkdir(exist_ok=True)한다.

# ── 인자 조립 ────────────────────────────────────────────────────────────────
# $Args가 아니라 $PyArgs — $Args는 PowerShell 자동 변수(바인딩 안 된 인자)라
# 대소문자 무구분으로 섀도잉된다. 이 로직을 함수로 감쌀 때 조용히 어긋난다.
$Module = if ($Mode -eq "gui") { "navi.gui" } else { "navi.daemon" }
$PyArgs = @()
if ($Mode -eq "stop") {
    $PyArgs += "stop"
} elseif ($Mode -ne "gui") {
    if ($Mode -eq "voice") {
        $PyArgs += "--voice"
        if (-not $NoWakeWord) { $PyArgs += "--wakeword" }
        # 불변 로케일로 직렬화 — 쉼표 소수점 로케일(de-DE 등)에서 [double] 37.5가
        # "37,5"가 되어 argparse float 파싱이 거부한다.
        $PyArgs += @("--vad-threshold", $VadThreshold.ToString([cultureinfo]::InvariantCulture))
        if ($Mic -ge 0) { $PyArgs += @("--mic", $Mic) }
    }
    if ($Brain)   { $PyArgs += @("--brain", $Brain) }
    if ($Mouth)   { $PyArgs += @("--mouth", $Mouth) }
    if ($Persona) { $PyArgs += @("--persona", $Persona) }
    if ($Db)      { $PyArgs += @("--db", $Db) }
    $PyArgs += $ExtraArgs
}

# ── 실행 (cwd = 프로젝트 루트) ────────────────────────────────────────────────
# Push-Location이라야 Ctrl+C로 끊어도 호출자의 쉘 위치가 남지 않는다.
# $code 선(先)초기화: pwsh 7.3+는 네이티브 비정상 종료를 terminating error로 던질 수
# 있어(PSNativeCommandUseErrorActionPreference) 대입 전에 빠져나갈 수 있다.
$code = 0
Push-Location $Proj
try {
    Write-Host "[$VenvDir] python -m $Module $($PyArgs -join ' ')" -ForegroundColor DarkGray
    & $PyExe -m $Module @PyArgs
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}

# stop의 exit 1은 "내릴 데몬이 없음"이라 정상 — 안내는 실행 모드에서만.
if ($code -ne 0 -and $Mode -ne "stop") {
    Write-Host "종료 코드 $code — 위 traceback을 확인하세요." -ForegroundColor Yellow
}
exit $code
