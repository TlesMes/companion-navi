"""부팅 전 판정 — "지금 이 머신에서 무엇으로 띄울 수 있나" (E6-2).

두 얼굴을 가진 하나의 모듈이다:

1. **환경·모델 doctor** — `python -m navi.preflight`. venv·API 키 같은 **설치 환경**과
   웨이크워드·TTS 가중치 같은 **모델 자산**이 갖춰졌는지, 뭐가 빠졌는지 본다
   (`flutter doctor`류의 진단 서브커맨드). 클론한 사람만이 아니라 유지보수자도 쓴다.
2. **런처 게이트** — GUI 실행 버튼(E6-4)이 띄우기 **전에** 부른다. 그 버튼은
   `DETACHED_PROCESS` + `DEVNULL`로 데몬을 띄우므로 부팅이 죽으면 **에러가 전부 사라진다**.
   자산 부족을 부팅 전에 잡는 것이 그 침묵을 메우는 유일한 값싼 방법이다.

**축은 엔진 하나다**(E6 확정 설계, gui.md). 부팅에서 되돌릴 수 없는 선택이 엔진뿐이라서다 —
톤·페르소나는 런타임에 교체된다. 그래서 `엔진 → 부팅 가능한 카드`를 답하고, 런처는 그 카드
이름을 `-Persona`로 넘긴다. **`--mouth`는 쓰지 않는다**: 그걸 주면 config.py의 카드 해석이
통째로 건너뛰어져 "나비 인격 + 아리스 목소리"라는 반쪽 정체성이 부팅 시점에 되살아난다.

## 읽기만 하고 혼자 돈다

**읽기 전용** — 파일 시스템과 환경변수를 볼 뿐 아무것도 바꾸지 않는다. 점검했다는 이유로
상태가 달라지는 일이 없다.
**단독 실행** — torch·마이크·포트·pid가 필요 없어 데몬이 떠 있지 않아도, GUI 프로세스
안에서도, 테스트에서도 그대로 돈다. 판정하려고 무언가를 띄울 필요가 없다는 뜻이다.

## 부팅 판정은 교체 판정과 다르다

`SwapRuntime.availability()`(E3)를 재사용하면 **안 된다**. 그쪽은 "런타임에 이 페르소나로
갈아탈 수 있나"라서 기본 톤 wav가 없으면 차단한다. 부팅은 그 경우 **warning만 찍고 뜬다**
(daemon.py — 카드 하나가 데몬을 벽돌로 만드는 게 E1에서 고친 실패 유형). 그래서 여기서는
**차단(blockers)과 경고(warnings)를 나눈다**. 다만 자산이 있는지 보는 눈은 같은 것을 쓴다
(`persona.missing_assets`) — 판정 이중화를 만들지 않기 위해서다.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from navi.config import load_config
from navi.persona import CharacterCard, missing_assets

log = logging.getLogger(__name__)

# 목소리 없이 뜨는 선택지의 식별자. 엔진 이름이 아니라 "엔진을 안 쓴다"는 뜻이라 따로 둔다.
TEXT_ONLY = "none"

# venv 규약 — scripts/run_navi.ps1이 고르는 것과 같은 경로.
# (스크립트가 venv 선택의 권위고 여기는 "있나 없나"만 본다)
_BASE_VENV = Path(".venv")
_VOICE_VENV = Path(".venv-voice")

# 키가 필요한 두뇌 → Config 속성명. echo는 키가 없어도 되므로 여기 없다.
_BRAIN_KEYS = {"anthropic": "anthropic_api_key", "gemini": "gemini_api_key"}


@dataclass
class CardOption:
    """한 카드를 그 카드가 결정한 엔진으로 띄울 수 있는가."""

    id: str
    character: str
    bootable: bool
    reason: str = ""          # 못 띄우는 이유(해법 포함) — bootable=False일 때만 채운다
    warnings: list[str] = field(default_factory=list)  # 떠도 나중에 물릴 것들


@dataclass
class EngineOption:
    """런처가 보여줄 선택지 하나 — 엔진 + 그 엔진으로 뜨는 카드들."""

    id: str                   # gptsovits | supertonic | none
    label: str
    bootable: bool            # 이 엔진으로 띄울 수 있는 카드가 하나라도 있는가
    reason: str = ""
    cards: list[CardOption] = field(default_factory=list)


@dataclass
class Prerequisites:
    """엔진과 무관하게 부팅을 막는 것들."""

    base_venv: bool           # .venv — GUI·텍스트 기동
    voice_venv: bool          # .venv-voice — 음성 스택(supertonic 포함)
    brains: dict[str, bool]   # 벤더 → 쓸 수 있나 (echo는 항상 True)
    wakeword_ready: bool
    wakeword_model: str | None

    @property
    def any_brain(self) -> bool:
        """실제 대화가 되는 두뇌가 하나라도 있나 — echo는 파이프라인 검증용이라 뺀다."""
        return any(ok for name, ok in self.brains.items() if name in _BRAIN_KEYS)


@dataclass
class PreflightReport:
    prerequisites: Prerequisites
    engines: list[EngineOption]

    def to_dict(self) -> dict:
        return {
            "prerequisites": asdict(self.prerequisites),
            "engines": [asdict(e) for e in self.engines],
        }


def _engine_label(engine: str) -> str:
    if engine == TEXT_ONLY:
        return "목소리 없이(점검용)"
    return engine


def _card_paths(root: Path) -> list[Path]:
    return sorted((root / "personas").glob("*.yaml"))


def _evaluate_card(root: Path, path: Path) -> tuple[str | None, CardOption]:
    """카드 하나를 판정해 (부팅될 엔진, 결과)를 낸다. 엔진이 None이면 카드 자체가 깨진 것.

    엔진을 직접 계산하지 않는다 — `load_config(persona_card=…)`가 데몬 부팅과 **같은 경로**로
    벤더를 해석하므로(config._vendor_from_card → persona.select_vendor) 그 결과를 그대로 쓴다.
    ckpt 경로 같은 벤더 옵션도 함께 나와 자산 검사 입력이 된다.
    """
    rel = path.relative_to(root).as_posix()
    try:
        config = load_config(root, persona_card=rel)
        card = CharacterCard.load(path, root=root)
    except Exception as exc:
        # 카드 하나가 깨져도 전체 진단을 죽이지 않는다(control/runtime.list_personas와 같은 규약).
        # 엔진 없음(None) — 어느 엔진 목록에도 넣지 않는다. 목소리 없이도 못 쓴다(카드가 깨졌으므로).
        log.debug("카드 판정 실패 — 건너뜀: %s", path, exc_info=True)
        # 사유는 한 줄로 — GUI 토스트 한 줄에 들어가야 한다(E8에서 잘림이 결정의 훼손이었다).
        # 전문은 로그에 있다.
        detail = str(exc).splitlines()[0] if str(exc).strip() else type(exc).__name__
        return None, CardOption(
            id=path.stem, character=path.stem, bootable=False,
            reason=f"카드를 읽을 수 없어요: {detail}",
        )

    engine = config.mouth.vendor
    vendor_voice = card.voice.vendor(engine) if card.voice else None
    blockers: list[str] = []
    warnings: list[str] = []

    missing = missing_assets(engine, vendor_voice)
    if missing.ckpts:
        blockers.append(
            "음색 가중치 파일이 없어요: "
            + ", ".join(Path(p).name for p in missing.ckpts)
        )
    if missing.tones:
        # 부팅은 된다 — 그 톤으로 말할 때 터진다. 차단이 아니라 경고인 이유.
        warnings.append(
            "레퍼런스 wav가 없는 톤: " + ", ".join(missing.tones) + " (그 톤으로 말하면 실패)"
        )

    if engine == "gptsovits":
        blockers.extend(_gptsovits_blockers(config, vendor_voice))

    return engine, CardOption(
        id=path.stem,
        character=card.character,
        bootable=not blockers,
        reason=" · ".join(blockers),
        warnings=warnings,
    )


def _gptsovits_blockers(config, vendor_voice) -> list[str]:
    """gptsovits만의 추가 조건 — 엔진 repo와 base 가중치.

    base 경로 지식은 어댑터가 소유한다(`mouth.gptsovits.missing_base_ckpts`) — 여기서 다시
    적으면 엔진과 판정이 갈라진다. 임포트는 가볍다(torch는 엔진 안에서 지연 로드).
    """
    from navi.mouth.gptsovits import missing_base_ckpts

    repo = config.mouth.options.get("repo_path") or ""
    if not repo or not os.path.isdir(repo):
        return [f"GPT-SoVITS repo가 없어요: {repo or '(config에 repo_path 미설정)'}"]

    # 카드가 fine-tune 가중치를 선언했으면 base는 안 쓴다(선언분은 위 missing_assets가 봄).
    declares_finetune = bool(vendor_voice and vendor_voice.gpt_ckpt and vendor_voice.sovits_ckpt)
    if declares_finetune:
        return []
    missing_base = missing_base_ckpts(repo)
    if missing_base:
        return [
            "base(zero-shot) 가중치가 없어요: "
            + ", ".join(os.path.basename(p) for p in missing_base)
            + " — GPT-SoVITS pretrained_models에 내려받아 주세요"
        ]
    return []


def evaluate(root: Path | None = None) -> PreflightReport:
    """이 머신의 부팅 가능성 전체 — 런처와 doctor가 함께 쓰는 진입점."""
    root = (root or Path.cwd()).resolve()

    # 전제조건은 카드와 무관하므로 기본 카드 기준으로 한 번만 읽는다.
    config = load_config(root)
    pre = Prerequisites(
        base_venv=(root / _BASE_VENV / "Scripts" / "python.exe").is_file(),
        voice_venv=(root / _VOICE_VENV / "Scripts" / "python.exe").is_file(),
        brains={
            "echo": True,
            **{name: bool(getattr(config, attr)) for name, attr in _BRAIN_KEYS.items()},
        },
        wakeword_ready=config.wakeword.ready,
        wakeword_model=config.wakeword.owww_model_path,
    )

    evaluated = [_evaluate_card(root, path) for path in _card_paths(root)]
    by_engine: dict[str, list[CardOption]] = {}
    for engine, option in evaluated:
        if engine is not None:
            by_engine.setdefault(engine, []).append(option)

    engines: list[EngineOption] = []
    for engine in sorted(by_engine):
        cards = by_engine[engine]
        blockers = []
        if not pre.voice_venv:
            blockers.append(f"음성 venv가 없어요({_VOICE_VENV}) — scripts\\setup\\setup_voice_env.ps1")
        if not pre.any_brain:
            blockers.append("두뇌 API 키가 없어요(.env) — ANTHROPIC_API_KEY 또는 GEMINI_API_KEY")
        if not blockers and not any(c.bootable for c in cards):
            blockers.append("이 엔진으로 뜨는 카드가 없어요")
        engines.append(
            EngineOption(
                id=engine, label=_engine_label(engine),
                bootable=not blockers, reason=" · ".join(blockers), cards=cards,
            )
        )

    # 목소리 없이 — 엔진을 안 쓰므로 음성 자산·venv가 무관하다(D17: 대화가 아니라 점검용 골격).
    text_blockers = []
    if not pre.base_venv:
        text_blockers.append(f"기본 venv가 없어요({_BASE_VENV})")
    if not pre.any_brain:
        text_blockers.append("두뇌 API 키가 없어요(.env)")
    engines.append(
        EngineOption(
            id=TEXT_ONLY, label=_engine_label(TEXT_ONLY),
            bootable=not text_blockers, reason=" · ".join(text_blockers),
            # 목소리를 안 건드리므로 음성 자산은 무관 — 깨진 카드만 빠진다.
            # 이름은 위에서 이미 읽은 것을 재사용한다(카드를 두 번 읽지 않는다).
            cards=[
                CardOption(
                    id=option.id, character=option.character,
                    bootable=engine is not None,
                    reason="" if engine is not None else option.reason,
                )
                for engine, option in evaluated
            ],
        )
    )
    return PreflightReport(prerequisites=pre, engines=engines)


def _render(report: PreflightReport) -> str:
    """사람이 읽는 진단 — 클론 직후 "뭐가 되나"의 답."""
    pre = report.prerequisites
    mark = lambda ok: "O" if ok else "X"  # noqa: E731
    lines = [
        "나비 부팅 점검",
        "",
        "전제조건",
        f"  [{mark(pre.base_venv)}] 기본 venv (.venv)",
        f"  [{mark(pre.voice_venv)}] 음성 venv (.venv-voice)",
        f"  [{mark(pre.wakeword_ready)}] 웨이크워드 모델  {pre.wakeword_model or ''}",
        "  두뇌: " + ", ".join(f"{n}[{mark(ok)}]" for n, ok in sorted(pre.brains.items())),
        "",
        "선택지 (엔진)",
    ]
    for engine in report.engines:
        lines.append(f"  [{mark(engine.bootable)}] {engine.label}"
                     + (f"  — {engine.reason}" if engine.reason else ""))
        for card in engine.cards:
            detail = f"  — {card.reason}" if card.reason else ""
            lines.append(f"        [{mark(card.bootable)}] {card.id} ({card.character}){detail}")
            for warn in card.warnings:
                lines.append(f"            ! {warn}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="navi-preflight",
        description="부팅 전 판정 — 무엇으로 띄울 수 있는지 (torch·마이크·포트 불요)",
    )
    parser.add_argument("--json", action="store_true", help="기계용 JSON 출력")
    args = parser.parse_args()

    # 파이프로 받으면(GUI·CI·리다이렉트) stdout이 로케일 인코딩(Windows 한글이면 cp949)이라
    # 한글 아닌 기호에서 UnicodeEncodeError로 죽는다 — 진단 도구가 진단 중에 죽으면 안 된다.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    report = evaluate()
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(_render(report))


if __name__ == "__main__":
    main()
