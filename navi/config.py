"""설정 로더 — config.yaml(공유 기본값) + config.local.yaml(머신 전용) + .env(비밀).

**세 층으로 나눈 이유(E6-3).** config.yaml은 커밋 파일이라 거기 담긴 값은 모든 머신에
강요된다. 그런데 마이크 임계처럼 **이 방·이 마이크에서 잰 값**은 남의 머신에서 틀리고,
그렇다고 커밋 안 되는 자리가 없으면 실행 스크립트 기본값에 박히게 된다(실제로 그랬다 —
run_navi.ps1의 -VadThreshold 50). config.local.yaml(gitignore)이 그 자리다.

층위: config.yaml(공유) < config.local.yaml(머신 전용) < CLI 인자(이번 실행만).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from navi.models import VoiceProfile

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BrainConfig:
    vendor: str  # gemini | anthropic | echo
    models: dict[str, str]  # vendor → model id

    @property
    def model(self) -> str:
        """현재 vendor에 맞는 모델 — 교체 시 vendor 한 줄만 바꾸면 되게."""
        return self.models[self.vendor]


@dataclass(frozen=True)
class MouthConfig:
    """TTS 어댑터 선택 + 나비의 목소리(VoiceProfile) + 벤더별 추가 kwargs.

    options는 create_mouth(vendor, **options)로 그대로 전달된다 — gptsovits의 repo/ckpt
    경로 등. 경로 옵션은 load_config에서 프로젝트 루트 기준 절대경로로 풀어둔다.
    """

    vendor: str  # fake | supertonic | gptsovits
    voice: VoiceProfile
    options: dict[str, Any]


@dataclass(frozen=True)
class WakeWordConfig:
    """웨이크워드(D7) 설정. engine으로 엔진 선택 — openwakeword(채택) | vosk | porcupine(보존).

    모델·키 파일이 아직 없어도 Config는 만들어진다 — 실제 사용은 CLI --wakeword 줄 때만.
    openwakeword의 한국어 모델만 **커밋 자산**(assets/wakeword/) — 자체 학습물이고, 없으면
    --wakeword 기동이 죽어 클론한 사람이 호출어를 못 쓰기 때문이다(E6-1).
    나머지(porcupine의 access_key는 .env, vosk·porcupine 모델·키워드 파일)는 재배포 불가라
    secrets/(커밋 금지)에 남는다.
    """

    engine: str
    keywords: tuple[str, ...]
    # openWakeWord (채택)
    owww_model_path: str | None  # 커스텀 한국어 .onnx
    owww_model_name: str | None  # 내장 영어 모델(예 "hey_jarvis") — 런타임 검증용
    threshold: float
    vad_threshold: float  # >0이면 Silero VAD로 비음성 출력 억제(오탐↓). CPU 절감 아님(D15 참조)
    # Vosk
    vosk_model_path: str | None
    # Porcupine 전용 (보존)
    access_key: str | None
    keyword_path: str | None
    model_path: str | None
    sensitivity: float
    active_timeout_ms: int

    @property
    def ready(self) -> bool:
        """선택한 엔진을 띄울 수 있는 최소 조건 — 모델/키 파일이 실제로 있는가까지 본다."""
        if self.engine == "openwakeword":
            # 커스텀 .onnx면 실존까지, 내장 영어 모델명이면 이름만으로 충분(런타임 검증용).
            if self.owww_model_path:
                return Path(self.owww_model_path).exists()
            return bool(self.owww_model_name)
        if self.engine == "vosk":
            return bool(
                self.keywords
                and self.vosk_model_path
                and Path(self.vosk_model_path).exists()
            )
        if self.engine == "porcupine":
            return bool(
                self.access_key
                and self.keyword_path
                and Path(self.keyword_path).exists()
            )
        return False


@dataclass(frozen=True)
class ModeConfig:
    """능동축(arch 5장) 기본값 — GUI(3단계)가 생기면 런타임 변경, 여긴 부팅 기본값.

    섹션이 없어도 기본값(23:00~07:00, 30분)으로 뜬다 — 기존 config.yaml 하위호환.
    """

    sleep_start: time
    sleep_end: time
    snooze_minutes: int


@dataclass(frozen=True)
class ProactiveConfig:
    """능동 발화 타이밍(arch 4.4 2층·7장) 기본값 — 전부 배선용 대충값이다.

    좋은 값은 종이로 못 정한다(진행 원칙 2): interaction_log가 쌓인 뒤 응답률·
    무시율을 보고 튜닝하는 게 후속 작업이고, 지금은 "값이 config에서 읽혀 함수로
    흘러든다"까지만 검증한다. 섹션이 없어도 기본값으로 뜬다(하위호환).
    """

    base_interval_s: float          # hazard 척도 λ의 기준(가중치 전) — 클수록 뜸하게
    min_gap_s: float                # 능동 발화 사이 최소 간격 — 이 안이면 확률 0
    daily_cap: int                  # 하루 최대 능동 발화 횟수 (원가/피로 방지)
    hazard_shape_k: float           # Weibull shape — >1이면 경과에 따라 발화 확률 상승
    time_weights: dict[str, float]  # 시간대(time_of_day) → 가중치, 클수록 자주


@dataclass(frozen=True)
class ControlConfig:
    """컨트롤 플레인(Stage 15) — 데몬 안 HTTP/WS 서버. 섹션이 없어도 기본값으로 뜬다."""

    enabled: bool
    port: int  # 127.0.0.1 고정 바인딩 — 호스트는 설정 대상이 아니다(gui.md 원칙)


@dataclass(frozen=True)
class Config:
    # 리포 루트 — persona 카드 voice 섹션 등 런타임에 상대경로를 풀 때의 기준.
    # gptsovits 웜업이 os.chdir를 하므로 "그때 가서 CWD 기준"은 성립하지 않는다.
    root: Path
    brain: BrainConfig
    mouth: MouthConfig
    wakeword: WakeWordConfig
    mode: ModeConfig
    proactive: ProactiveConfig
    control: ControlConfig
    db_path: Path
    recent_turns: int
    persona_card_path: Path
    gemini_api_key: str | None
    anthropic_api_key: str | None
    # 마이크 입력 1관문 — RMS 에너지 VAD 임계(D15 캐스케이드의 energy 층).
    # config `ear.energy_vad_threshold`, 미지정·0이면 CLI에서 안 준 것과 같은 취급이라
    # daemon이 자기 기본(EnergyVad=150)으로 뜬다(mic.py:46, listening.py:62의 `vad or EnergyVad()`).
    # **wakeword.vad_threshold와 다른 손잡이다** — 그쪽은 openWakeWord 내부 Silero VAD로
    # 호출어 오탐을 억제하고(wakeword.py:209), 이쪽은 마이크 프레임이 STT로 넘어갈지를 가른다.
    # 마이크 게인·방 소음을 타는 머신 전용값이라 config.local.yaml이 제자리다(E6-3).
    # 기본값을 둔 건 필드 추가로 기존 생성부가 안 깨지게 하려는 것.
    energy_vad_threshold: float = 0.0


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """오버레이를 base 위에 재귀 병합 — dict끼리만 파고들고 나머지는 통째로 교체.

    얕은 병합이면 `ear:` 한 줄 덮으려다 `ear` 아래 전체(wakeword 설정 등)를 날린다.
    오버레이는 "한 값만 바꾸는" 용도라 그 사고가 기본 동작이면 안 된다. 리스트를 병합하지
    않고 교체하는 것도 같은 이유 — 원소 단위 병합은 의도가 모호하다.
    """
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_raw(root: Path) -> dict[str, Any]:
    """config.yaml 위에 config.local.yaml(있으면)을 덮은 설정 원본.

    로컬 파일은 gitignore라 **없는 것이 정상**이다 — 없으면 base 그대로. 있는데 매핑이
    아니면 무시(잘못된 파일 하나로 데몬을 못 띄우는 게 더 나쁘다). 비어 있으면(`{}`) 무영향.
    """
    raw = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    local_path = root / "config.local.yaml"
    if not local_path.exists():
        return raw
    local = yaml.safe_load(local_path.read_text(encoding="utf-8"))
    if not isinstance(local, dict):
        if local is not None:  # None은 빈 파일 — 조용히 통과
            log.warning("config.local.yaml이 매핑이 아니라 무시함: %s", local_path)
        return raw
    log.info("config.local.yaml 적용 — 머신 전용 설정이 config.yaml을 덮는다")
    return _deep_merge(raw, local)


def _resolve(root: Path, value: str) -> str:
    """경로 옵션을 루트 기준 절대경로로. 이미 절대경로(예: C:\\gptsovits)면 그대로 둔다."""
    return str((root / value).resolve()) if value else value


def _vendor_from_card(root: Path, card_path: Path, config_default: str) -> str:
    """활성 카드의 목소리 번들에서 TTS 벤더를 해석한다. 실패하면 config 기본.

    카드의 `voice:` 섹션만 필요하므로 CharacterCard 전체를 짓지 않는다 — profiles가
    비었거나 스키마가 어긋난 카드도 목소리 번들은 멀쩡할 수 있고, 그 판정은 여기가
    아니라 카드를 실제로 쓰는 쪽(daemon._run)의 몫이다.

    **깨진 카드를 여기서 삼켜도 부팅이 구제되지는 않는다** — daemon._run이 곧바로
    같은 파일을 CharacterCard.load로 읽다 죽는다(의도된 fail-fast: 페르소나 없이
    돌 수 있는 데몬은 없다). 이 except의 목적은 그 실패를 *가리는* 게 아니라
    **벤더 해석이 새로운 실패 지점이 되지 않게** 하는 것뿐이라, 진짜 사인을 보고할
    daemon 쪽과 겹쳐 시끄럽지 않도록 debug로만 남긴다.
    """
    from navi.persona import PersonaVoice, select_vendor

    try:
        raw = yaml.safe_load(card_path.read_text(encoding="utf-8")) or {}
        section = raw.get("voice")
        voice = PersonaVoice.parse(section, root=root) if section else None
    except Exception:
        log.debug("벤더 해석용 카드 읽기 실패 — config 기본 사용: %s", card_path, exc_info=True)
        return config_default
    return select_vendor(voice, config_default=config_default)


def _load_mouth(root: Path, raw: dict[str, Any]) -> MouthConfig:
    raw_mouth = raw.get("mouth", {})
    vendor = raw_mouth.get("vendor", "fake")
    mv = raw_mouth.get("voice", {})
    # 벤더 이름과 같은 하위 섹션 = 추가 kwargs + 그 벤더의 음색(voice_id).
    # voice_id를 벤더 섹션에 두면 두 벤더 설정이 공존해도 음색이 섞이지 않아
    # config 수정 없이 --mouth 로 무중단 교체된다.
    options = dict(raw_mouth.get(vendor, {}))
    # voice_id는 VoiceProfile로 빠지므로 create_mouth kwargs에서 제거한다.
    voice_id = options.pop("voice_id", "") or mv.get("vendor_voice_id", "")
    # gptsovits는 voice_id가 레퍼런스 wav 경로 → 절대경로로 풀어둔다.
    # supertonic은 음색 이름(F1 등)이라 _resolve가 그대로 통과시킨다(파일이 아니어도 무해).
    if vendor == "gptsovits" and voice_id:
        voice_id = _resolve(root, voice_id)
    voice = VoiceProfile(
        name=mv.get("name", "navi"),
        vendor_voice_id=voice_id,
        speed=float(mv.get("speed", 1.0)),
    )
    for key in ("repo_path", "gpt_ckpt", "sovits_ckpt"):
        if options.get(key):
            options[key] = _resolve(root, options[key])
    return MouthConfig(vendor=vendor, voice=voice, options=options)


def _load_wakeword(root: Path, raw: dict[str, Any]) -> WakeWordConfig:
    ww = raw.get("ear", {}).get("wakeword", {})
    owww = ww.get("openwakeword", {})
    vosk = ww.get("vosk", {})
    porc = ww.get("porcupine", {})
    owww_model = owww.get("model_path")
    vosk_model = vosk.get("model_path")
    pkw = porc.get("keyword_path")
    pmodel = porc.get("model_path")
    # 경로만 루트 기준 절대화(파일이 없어도 resolve는 무해). openwakeword 모델은 커밋
    # 자산(assets/wakeword/), vosk·porcupine 것은 secrets/ — 위 docstring 참조.
    return WakeWordConfig(
        engine=ww.get("engine", "openwakeword"),
        keywords=tuple(vosk.get("keywords") or ()),  # Vosk 전용(호출어 포함 매칭)
        owww_model_path=_resolve(root, owww_model) if owww_model else None,
        owww_model_name=owww.get("model_name") or None,
        threshold=float(owww.get("threshold", 0.5)),
        vad_threshold=float(owww.get("vad_threshold", 0.0)),
        vosk_model_path=_resolve(root, vosk_model) if vosk_model else None,
        access_key=os.getenv("PICOVOICE_ACCESS_KEY") or None,
        keyword_path=_resolve(root, pkw) if pkw else None,
        model_path=_resolve(root, pmodel) if pmodel else None,
        sensitivity=float(porc.get("sensitivity", 0.5)),
        active_timeout_ms=int(ww.get("active_timeout_ms", 30000)),
    )


def _load_control(raw: dict[str, Any]) -> ControlConfig:
    c = raw.get("control", {})
    return ControlConfig(
        enabled=bool(c.get("enabled", True)),
        port=int(c.get("port", 8765)),
    )


def _load_mode(raw: dict[str, Any]) -> ModeConfig:
    m = raw.get("mode", {})
    window = m.get("sleep_window", {})
    return ModeConfig(
        sleep_start=time.fromisoformat(window.get("start", "23:00")),
        sleep_end=time.fromisoformat(window.get("end", "07:00")),
        snooze_minutes=int(m.get("snooze_minutes", 30)),
    )


def _load_proactive(raw: dict[str, Any]) -> ProactiveConfig:
    p = raw.get("proactive", {})
    weights = p.get("time_weights") or {
        "morning": 1.2,
        "afternoon": 1.0,
        "evening": 1.1,
        "night": 0.5,
    }
    return ProactiveConfig(
        base_interval_s=float(p.get("base_interval_s", 3600)),
        min_gap_s=float(p.get("min_gap_s", 1800)),
        daily_cap=int(p.get("daily_cap", 8)),
        hazard_shape_k=float(p.get("hazard_shape_k", 2.0)),
        time_weights={k: float(v) for k, v in weights.items()},
    )


def load_config(
    root: Path | None = None,
    *,
    mouth_vendor: str | None = None,
    persona_card: str | None = None,
) -> Config:
    """config.yaml + .env를 합쳐 불변 Config로.

    mouth_vendor·persona_card는 이번 실행만의 오버라이드(CLI --mouth/--persona) —
    벤더 섹션을 다시 읽어야 해서 후처리 replace()로는 안 되므로 여기서 주입한다.
    """
    root = root or Path.cwd()
    load_dotenv(root / ".env")
    raw = _load_raw(root)
    card_path = root / (persona_card or raw["persona"]["card_path"])
    if mouth_vendor:
        raw.setdefault("mouth", {})["vendor"] = mouth_vendor
    else:
        # 카드가 목소리 번들을 소유하면 벤더도 카드가 정한다(2026.07.10 결정).
        # _load_mouth가 벤더 하위 섹션을 읽으므로 그 전에 확정해야 하고, 여기서
        # 정하면 mouth.options·SwapRuntime·부팅 톤이 한 번에 일관된다.
        raw.setdefault("mouth", {})["vendor"] = _vendor_from_card(
            root, card_path, raw.get("mouth", {}).get("vendor", "fake")
        )
    return Config(
        root=root.resolve(),
        brain=BrainConfig(
            vendor=raw["brain"]["vendor"],
            models=dict(raw["brain"]["models"]),
        ),
        mouth=_load_mouth(root, raw),
        wakeword=_load_wakeword(root, raw),
        mode=_load_mode(raw),
        proactive=_load_proactive(raw),
        control=_load_control(raw),
        db_path=root / raw["db"]["path"],
        recent_turns=int(raw["memory"]["recent_turns"]),
        persona_card_path=card_path,
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        energy_vad_threshold=float(raw.get("ear", {}).get("energy_vad_threshold", 0.0)),
    )
