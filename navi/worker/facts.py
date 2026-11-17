"""사실 추출 작업 — 단독 모드 FactExtractor와 같은 프롬프트·파서·검증을 서버 작업에 건다.

다른 점은 셋뿐이다:
- 입력(대화·이미 아는 사실)을 서버가 준다. 확정된 발화만, 아직 안 끝난 턴 앞까지.
- 날짜는 이 기기가 아니라 **그 사용자의** 시간대로 센다(서버가 오프셋을 준다).
- 결과를 DB에 쓰지 않고 서버에 돌려준다. 서버가 번호를 한 번 더 거른다.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from navi.brain.base import BrainAdapter
from navi.feed.summarize import drain
from navi.memory.facts import FACT_SYSTEM, build_fact_prompt, parse_facts, select_facts
from navi.models import Fact, LlmRequest, Message, Turn

log = logging.getLogger(__name__)


def _turns(payload: dict[str, Any]) -> list[Turn]:
    return [
        Turn(
            role=t["role"],
            text=t["text"],
            created_at=datetime.fromisoformat(t["created_at"].replace("Z", "+00:00")),
            session_id="server",
            turn_id=int(t["id"]),  # 서버 발화 ID — 프롬프트의 T번호·출처가 이것이다
        )
        for t in payload.get("turns", [])
    ]


def _known(payload: dict[str, Any], now: datetime) -> list[Fact]:
    return [
        Fact(
            fact_id=int(f["id"]), kind=f["kind"], text=f["text"], source_turn_id=None,
            status="active", expires_at=None, created_at=now,
        )
        for f in payload.get("known_facts", [])
    ]


async def run_fact_job(
    payload: dict[str, Any], *, extractor: BrainAdapter, model: str
) -> dict[str, Any]:
    """작업 하나를 처리해 서버에 돌려줄 결과를 만든다. LLM 실패는 예외로 올린다(호출부가 fail).

    뽑은 게 없어도 커서는 옮긴다 — "기억할 게 없다"도 정당한 답이고, 안 옮기면 같은 대화를
    매번 다시 보낸다. 사용자 말이 하나도 없는 묶음은 LLM을 부르지 않고 커서만 옮긴다.
    """
    now = datetime.fromisoformat(payload["now"].replace("Z", "+00:00")).astimezone(UTC)
    tz = timezone(timedelta(minutes=int(payload.get("utc_offset_minutes", 0))))
    turns = _turns(payload)
    if not turns:
        return {"facts": [], "cursor": None, "usage": {"input_tokens": 0, "output_tokens": 0}}
    cursor = turns[-1].turn_id
    user_turns = [t for t in turns if t.role == "user"]
    if not user_turns:
        return {"facts": [], "cursor": cursor, "usage": {"input_tokens": 0, "output_tokens": 0}}

    known = _known(payload, now)
    request = LlmRequest(
        system=FACT_SYSTEM,
        messages=[Message(role="user", text=build_fact_prompt(turns, known, now, tz))],
        model=model,
    )
    reply = await drain(extractor, request)
    usage = extractor.last_result.usage if extractor.last_result else None

    selected = select_facts(
        parse_facts(reply, int(payload.get("max_facts", 10))),
        batch_ids={t.turn_id for t in turns},
        fallback_src=user_turns[-1].turn_id,
        known=known,
        now=now,
        tz=tz,
    )
    log.info("사실 추출 — 대화 %d행에서 %d건", len(turns), len(selected))
    return {
        "facts": [
            {
                "kind": f.kind,
                "text": f.text,
                "source_id": f.source_turn_id,
                "expires_at": f.expires_at.isoformat() if f.expires_at else None,
                "replaces": f.replaces,
            }
            for f in selected
        ],
        "cursor": cursor,
        "usage": {
            "input_tokens": usage.input_tokens if usage else 0,
            "output_tokens": usage.output_tokens if usage else 0,
        },
    }
