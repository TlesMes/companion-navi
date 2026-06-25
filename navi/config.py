"""설정 로더 — config.yaml(튜닝값) + .env(비밀)를 합쳐 불변 Config로."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from navi.models import VoiceProfile


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
    """웨이크워드(D7) 설정. engine으로 엔진 선택 — vosk(채택) | porcupine(보존).

    모델·키 파일이 아직 없어도 Config는 만들어진다 — 실제 사용은 CLI --wakeword 줄 때만.
    porcupine의 access_key는 비밀(.env), 모델·키워드 파일 경로는 secrets/(커밋 금지).
    """

    engine: str
    keywords: tuple[str, ...]
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
class Config:
    brain: BrainConfig
    mouth: MouthConfig
    wakeword: WakeWordConfig
    db_path: Path
    recent_turns: int
    persona_card_path: Path
    gemini_api_key: str | None
    anthropic_api_key: str | None


def _resolve(root: Path, value: str) -> str:
    """경로 옵션을 루트 기준 절대경로로. 이미 절대경로(예: C:\\gptsovits)면 그대로 둔다."""
    return str((root / value).resolve()) if value else value


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
    vosk = ww.get("vosk", {})
    porc = ww.get("porcupine", {})
    vosk_model = vosk.get("model_path")
    pkw = porc.get("keyword_path")
    pmodel = porc.get("model_path")
    # 모델·키 파일은 secrets/ — 경로만 루트 기준 절대화(파일이 없어도 resolve는 무해).
    return WakeWordConfig(
        engine=ww.get("engine", "vosk"),
        keywords=tuple(ww.get("keywords") or ()),
        vosk_model_path=_resolve(root, vosk_model) if vosk_model else None,
        access_key=os.getenv("PICOVOICE_ACCESS_KEY") or None,
        keyword_path=_resolve(root, pkw) if pkw else None,
        model_path=_resolve(root, pmodel) if pmodel else None,
        sensitivity=float(porc.get("sensitivity", 0.5)),
        active_timeout_ms=int(ww.get("active_timeout_ms", 30000)),
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
    raw = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    if mouth_vendor:
        raw.setdefault("mouth", {})["vendor"] = mouth_vendor
    return Config(
        brain=BrainConfig(
            vendor=raw["brain"]["vendor"],
            models=dict(raw["brain"]["models"]),
        ),
        mouth=_load_mouth(root, raw),
        wakeword=_load_wakeword(root, raw),
        db_path=root / raw["db"]["path"],
        recent_turns=int(raw["memory"]["recent_turns"]),
        persona_card_path=root / (persona_card or raw["persona"]["card_path"]),
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
    )
