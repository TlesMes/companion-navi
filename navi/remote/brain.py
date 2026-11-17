"""연결 모드의 두뇌·조립 — 파이프라인이 쓰는 두 경계 뒤에 서버를 끼운다.

`TurnPipeline`은 `conductor.build_request(...)`로 요청을 만들고 `brain.generate_stream(...)`으로
토큰을 받는다. 단독 모드에서는 그 둘이 로컬 조립과 벤더 호출이다. 연결 모드에서는:

- `RemoteConductor`는 조립하지 않는다. 발화·턴 종류·페르소나 키만 담은 요청을 만든다.
- `RemoteBrain`은 그 요청을 서버에 `turn.start`로 보내고 돌아오는 조각을 흘린다.

파이프라인·TTS·컨트롤 플레인은 어느 모드인지 모른다. 이 모듈만 프로토콜을 안다.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from navi.brain.base import BrainAdapter, BrainUnavailable
from navi.models import BrainResult, LlmRequest, Message, TurnKind, Usage
from navi.persona import CharacterCard
from navi.remote.link import ServerError, ServerLink

log = logging.getLogger(__name__)

_CONFIRM_TIMEOUT_S = 5


@dataclass(frozen=True)
class RemoteTurnRequest(LlmRequest):
    """서버로 보낼 턴 — 조립된 프롬프트가 아니라 조립의 **재료**다.

    LlmRequest를 잇는 건 파이프라인이 그 타입으로 주고받기 때문이다. system은 비어 있고
    messages에는 이번 트리거 하나뿐이다. 카드와 대화 이력은 서버가 붙인다.
    """

    kind: TurnKind = TurnKind.REACTIVE
    persona: str = ""


class RemoteConductor:
    """`Conductor`의 연결 모드 짝 — 같은 메서드, 조립은 안 한다.

    카드는 여전히 로컬에서 읽는다. 목소리(음색·톤·언어)가 카드의 `voice` 섹션에 있고 그건
    이 기기의 것이다. 서버가 쓰는 건 카드의 글이고, 서버에는 키만 보낸다.
    """

    def __init__(self, card: CharacterCard, persona_id: str):
        self._card = card
        self._persona_id = persona_id

    @property
    def card(self) -> CharacterCard:
        return self._card

    def set_card(self, card: CharacterCard, persona_id: str | None = None) -> None:
        log.info("카드 교체: %s → %s", self._card.character, card.character)
        self._card = card
        if persona_id is not None:
            self._persona_id = persona_id

    def build_request(
        self,
        trigger_text: str,
        user_id: int,
        session_id: str,
        *,
        kind: TurnKind = TurnKind.REACTIVE,
    ) -> RemoteTurnRequest:
        # user_id·session_id는 로컬 DB의 것이라 쓰지 않는다 — 서버는 토큰으로 사용자를 안다.
        return RemoteTurnRequest(
            system="",
            messages=[Message(role="user", text=trigger_text)],
            model="(server)",
            kind=kind,
            persona=self._persona_id,
        )


class RemoteBrain(BrainAdapter):
    """서버에 턴을 보내고 응답 조각을 흘리는 두뇌.

    서버는 무드를 구조화 필드로 따로 보낸다(protocol.md §3.2). 이 어댑터는 그것을
    `[mood:key] ` 선행 태그로 **되돌려** 흘린다 — `BrainAdapter`의 계약이 "맨 앞에 무드
    태그가 붙은 텍스트"이고 파이프라인이 그 태그를 떼어 톤을 고르기 때문이다. 태그를 다시
    만드는 게 어색해 보여도, 이렇게 해야 단독 모드와 같은 경로를 한 글자도 안 바꾸고 탄다.

    턴이 끝나면 호출부가 `report_played`로 재생 결과를 알려야 한다. 서버는 그걸 받아
    턴을 확정한다(G1). 안 알리면 서버는 연결이 닫힐 때 전문으로 확정한다.
    """

    def __init__(self, link: ServerLink):
        super().__init__()
        self._link = link
        self._turn_id: str | None = None  # 지금 생성 중인 턴
        self._pending: tuple[str, int] | None = None  # 재생 보고를 기다리는 턴 (id, 글자 수)
        self._interrupted = False

    async def validate(self, model: str) -> None:
        """서버에 붙을 수 있는지만 본다 — 턴을 보내면 쿼터를 쓴다."""
        await self._link.connect()

    async def generate_stream(self, request: LlmRequest) -> AsyncIterator[str]:
        if not isinstance(request, RemoteTurnRequest):
            raise TypeError("RemoteBrain은 RemoteConductor가 만든 요청만 받는다")
        self.last_result = None
        self._cancelled = False
        self._interrupted = False
        self._pending = None

        await self._link.connect()
        turn_id = str(uuid.uuid4())
        self._turn_id = turn_id
        body: list[str] = []
        expected_seq = 0
        try:
            await self._link.send(
                {
                    "type": "turn.start",
                    "turn_id": turn_id,
                    "kind": request.kind.value,
                    "persona": request.persona,
                    "text": request.messages[-1].text,
                }
            )
            while True:
                msg = await self._link.recv()
                if msg.get("turn_id") != turn_id:
                    continue  # 앞선 턴의 늦은 메시지(확정 응답 등)
                kind = msg["type"]
                if kind == "turn.mood":
                    yield f"[mood:{msg['mood']}] "
                elif kind == "turn.delta":
                    if msg["seq"] != expected_seq:
                        # 이어받기가 없으니(D22) 조각이 빠졌으면 이 턴은 못 쓴다.
                        raise BrainUnavailable(
                            f"응답 조각이 빠졌어요(seq {msg['seq']}, 기대 {expected_seq})"
                        )
                    expected_seq += 1
                    body.append(msg["text"])
                    yield msg["text"]
                elif kind == "turn.end":
                    usage = msg.get("usage", {})
                    self.last_result = BrainResult(
                        full_text="".join(body),
                        usage=Usage(
                            input_tokens=int(usage.get("input_tokens", 0)),
                            output_tokens=int(usage.get("output_tokens", 0)),
                        ),
                    )
                    self._pending = (turn_id, int(msg["chars"]))
                    # 취소를 보냈는데 끝이 먼저 왔다 — 턴은 끝까지 생성됐고 사용자가 말을
                    # 끊은 것이다. 재생 보고가 어디까지 들렸는지를 알린다.
                    self._interrupted = self._cancelled
                    return
                elif kind == "turn.cancelled":
                    # 서버가 이 턴을 폐기했다. 결과가 없으니 호출부도 기억에 남기지 않는다.
                    return
                elif kind == "turn.error":
                    raise ServerError(msg["code"], msg.get("message", ""), msg.get("retry_after_s"))
        finally:
            self._turn_id = None

    def cancel(self) -> None:
        """말 끊기. 생성 중이면 서버에 취소를 보내고, 이미 끝났으면 끊겼다는 것만 적어 둔다."""
        self._cancelled = True
        if self._turn_id is not None:
            # cancel()은 동기 계약이다(파이프라인의 kill switch). 전송은 루프에 맡긴다.
            try:
                asyncio.get_running_loop().create_task(self._send_cancel(self._turn_id))
            except RuntimeError:
                pass  # 루프 밖 — 보낼 길이 없다. 연결이 닫히면 서버가 폐기한다
        elif self._pending is not None:
            self._interrupted = True

    async def _send_cancel(self, turn_id: str) -> None:
        try:
            await self._link.send({"type": "turn.cancel", "turn_id": turn_id})
        except BrainUnavailable:
            pass  # 연결이 이미 죽었다 — 서버가 그 턴을 폐기한다(D22)

    async def report_played(self, played_chars: int | None) -> None:
        """재생 결과를 서버에 알린다 — 서버가 들린 데까지 잘라 턴을 확정한다(G1).

        played_chars는 TTS가 끝까지 소리 낸 본문 길이(코드 포인트)다. None은 모른다는 뜻:

        - 끊기지 않았으면 전문이 들린 것이다(값을 안 본다).
        - 끊겼는데 어디까지 들렸는지 모르면 **0으로 보고한다.** 사용자가 못 들은 말을 나비가
          "했다"고 기억하는 것보다 한 말을 잊는 쪽이 덜 나쁘다 — 턴 확정이 있는 이유가 그것이다.
        """
        if self._pending is None:
            return
        (turn_id, total), self._pending = self._pending, None
        if not self._interrupted:
            played = total
        elif played_chars is None:
            played = 0
        else:
            played = max(0, min(played_chars, total))
        try:
            await self._link.send(
                {
                    "type": "turn.played",
                    "turn_id": turn_id,
                    "played_chars": played,
                    "interrupted": played < total,
                }
            )
            async with asyncio.timeout(_CONFIRM_TIMEOUT_S):
                while True:
                    msg = await self._link.recv()
                    if msg.get("type") == "turn.confirmed" and msg.get("turn_id") == turn_id:
                        break
        except (BrainUnavailable, TimeoutError):
            # 보고가 안 갔다. 서버는 연결이 닫힐 때 전문으로 확정한다 — 여기서 할 수 있는 건 없다.
            log.warning("재생 보고를 서버에 전하지 못했어요(turn=%s)", turn_id)
            return
        if played < total:
            log.info("재생 보고 — %d/%d자에서 끊김", played, total)


def persona_id_of(card_path: Path) -> str:
    """카드 파일 → 서버에 보낼 페르소나 키. 서버도 파일 이름으로 카드를 찾는다."""
    return card_path.stem
