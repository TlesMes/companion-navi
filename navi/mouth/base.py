"""출력(Mouth) 어댑터 계약 (01 문서 4.8절 — 스트리밍 TTS).

두뇌의 토큰 스트림을 받아 음성으로 합성·즉시 재생한다. 첫 오디오 ~1초가 목표라 토큰이
다 모이길 기다리지 않고 들어오는 대로 합성한다. 목소리(voice_profile)는 전 모드 공통
단일값 — 나비의 정체성이라 TTS 벤더(수퍼톤·Cartesia…)를 갈아껴도 같은 목소리여야 한다
(설계 원칙 2). 벤더는 이 계약 뒤에 숨는다.

사용 규약:
- speak_stream은 재생이 끝날 때까지 await한다.
- stop()은 barge-in(말 끊기)용 — 재생을 즉시 중단한다 (계약상 필수).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from navi.models import VoiceProfile


class MouthAdapter(ABC):
    @abstractmethod
    async def speak_stream(
        self, tokens: AsyncIterator[str], voice: VoiceProfile
    ) -> None:
        """두뇌의 토큰 스트림을 합성·즉시 재생. 재생 완료까지 await한다."""

    @abstractmethod
    def stop(self) -> None:
        """barge-in 시 재생을 즉시 중단한다 (계약상 필수)."""

    @abstractmethod
    def is_playing(self) -> bool:
        """현재 재생 중인지 — 턴테이킹이 구독한다."""

    def warmup(self) -> None:
        """엔진을 미리 로드한다. 기본: no-op. 지연 로드 어댑터에서 재정의."""

    def set_weights(
        self, gpt_ckpt: str, sovits_ckpt: str, *, ref_lang: str = "", gen_lang: str = ""
    ) -> None:
        """음색 가중치를 런타임 교체한다 (페르소나 번들 전환). 기본: 미지원.

        언어는 가중치와 한 몸이라 함께 받는다 — 빈 값이면 현재 언어를 유지한다(부팅 배선과
        같은 규칙). 블로킹 호출(수 초의 모델 로드)이라 호출부가 스레드로 넘긴다
        (TurnPipeline.swap_weights). 가중치를 갖지 않는 엔진(프리셋 기반)은 재정의하지 않는다.
        """
        raise NotImplementedError(f"{type(self).__name__}은 가중치 핫스왑 미지원")
