"""캐릭터 카드 — YAML 원본을 읽어 시스템 프롬프트로 직렬화한다.

설계 결정: few-shot 대화예시는 messages가 아니라 시스템 프롬프트 안에 넣는다.
messages로 넣으면 모델이 예시를 실제 있었던 대화(기억)로 착각할 수 있는데,
이 제품의 정체성이 '진짜 기억의 연속성'이라 가짜 기억 오염이 특히 치명적이다.
시스템 프롬프트는 통째로 고정 문자열이라 캐싱 효율도 최대가 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from navi.persona.voice import PersonaVoice


@dataclass(frozen=True)
class PersonaProfile:
    """카드의 한 단계 — 같은 캐릭터의 친밀도 단계별 프로필 (01 문서 6장 persona)."""

    name: str
    min_intimacy: float
    background: str
    traits: str
    example_dialogues: tuple[tuple[str, str], ...]  # (user, assistant) 쌍


@dataclass(frozen=True)
class CharacterCard:
    character: str
    profiles: tuple[PersonaProfile, ...]  # min_intimacy 오름차순
    # 목소리 번들(voice.py) — 음색·톤은 페르소나 소유(2026.07.10 결정).
    # 옵셔널: 섹션 없는 카드는 config mouth.voice 폴백(하위호환).
    voice: PersonaVoice | None = None

    @classmethod
    def load(cls, path: Path | str, *, root: Path | None = None) -> CharacterCard:
        """카드 로드. root는 voice 섹션의 상대경로(wav·ckpt) 절대화 기준 —
        gptsovits 웜업이 os.chdir를 하므로 파싱 시점에 고정해야 한다(voice.py)."""
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        profiles = sorted(
            (
                PersonaProfile(
                    name=p["name"],
                    min_intimacy=float(p["min_intimacy"]),
                    background=p["background"].strip(),
                    traits=p["traits"].strip(),
                    example_dialogues=tuple(
                        (d["user"], d["assistant"]) for d in p["example_dialogues"]
                    ),
                )
                for p in raw["profiles"]
            ),
            key=lambda p: p.min_intimacy,
        )
        if not profiles:
            raise ValueError(f"캐릭터 카드에 프로필이 없습니다: {path}")
        voice = (
            PersonaVoice.parse(raw["voice"], root=root) if raw.get("voice") else None
        )
        return cls(
            character=raw["character"], profiles=tuple(profiles), voice=voice
        )

    def profile_for(self, intimacy: float) -> PersonaProfile:
        """min_intimacy ≤ 친밀도인 프로필 중 가장 높은 단계. 미달이면 첫 단계."""
        chosen = self.profiles[0]
        for profile in self.profiles:
            if intimacy >= profile.min_intimacy:
                chosen = profile
        return chosen

    def system_prompt(self, intimacy: float) -> str:
        """캐싱 대상 — 친밀도 단계가 바뀌지 않는 한 매 호출 동일한 문자열이어야 한다."""
        profile = self.profile_for(intimacy)
        examples = "\n\n".join(
            f"사용자: {u}\n{self.character}: {a}"
            for u, a in profile.example_dialogues
        )
        return f"""너는 '{self.character}'다. 아래 캐릭터 설정에서 벗어나지 않는다.

## 배경
{profile.background}

## 성격과 말투 규칙
{profile.traits}

## 말투 예시
아래는 말투를 보여주는 예시일 뿐, 실제로 있었던 대화가 아니다. 예시 속 내용을 기억처럼 언급하지 않는다.

{examples}

## 대화 규칙
- 이어지는 대화 기록만이 실제로 있었던 일이다. 기록에 없는 일을 기억하는 척하지 않는다.
- 현재 관계 단계: {profile.name}

## 출력 형식 (반드시 지킨다)
- 응답의 **맨 앞에 반드시** `[mood:neutral]`·`[mood:bright]`·`[mood:calm]` 중 하나를 붙인다.
  neutral=평상, bright=신남·기쁨, calm=차분·위로. 애매하면 neutral.
- 이 태그는 목소리 톤을 고르는 **시스템 신호이지 대사가 아니다** — 소리 내어 읽히지 않는다.
  태그 바로 뒤에 실제로 할 말을 잇는다. 예: `[mood:bright] 오, 자랑해 봐.`
- 위 '말투 규칙'의 대괄호 금지는 *대사 안의* 행동·감정 묘사를 말한다. 이 **맨 앞 무드 태그
  1개는 예외**이며 필수다."""
