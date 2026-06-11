"""Phase 1의 입과 귀 — CLI 텍스트 대화 루프.

실행: python -m navi.cli [--brain gemini|anthropic|echo]
종료: /quit 또는 Ctrl+C. 실행마다 새 session_id를 발급하지만
단기기억은 세션 경계 없이 인출하므로 껐다 켜도 직전 대화가 이어진다.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from dataclasses import replace

from navi.brain import create_brain
from navi.conductor import Conductor
from navi.config import Config, load_config
from navi.memory import MemoryStore
from navi.persona import CharacterCard


async def chat(config: Config) -> None:
    store = MemoryStore(config.db_path)
    card = CharacterCard.load(config.persona_card_path)
    brain = create_brain(config)
    conductor = Conductor(card=card, memory=store, config=config)
    user_id = store.ensure_user(display_name="친구")
    session_id = uuid.uuid4().hex

    print(
        f"{card.character} 깨어남 — 두뇌: {config.brain.vendor}({config.brain.model})."
        " /quit 으로 종료."
    )
    try:
        while True:
            try:
                raw = await asyncio.to_thread(input, "\n나> ")
                text = raw.strip("﻿ \t\r\n")  # BOM: 파이프 입력 인코딩 방어
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not text:
                continue
            if text in {"/quit", "/exit"}:
                break

            request = conductor.build_request(
                text, user_id=user_id, session_id=session_id
            )
            print(f"{card.character}> ", end="", flush=True)
            async for token in brain.generate_stream(request):
                print(token, end="", flush=True)  # 전 구간 스트리밍의 텍스트 버전
            print()

            result = brain.last_result
            if result is None:  # 스트림이 결과 없이 끊긴 경우 — 기억에 남기지 않는다
                continue
            store.append_turn(session_id, user_id, role="user", text=text)
            store.append_turn(session_id, user_id, role="assistant", text=result.full_text)
            store.log_usage("llm", result.usage)
    finally:
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="navi", description="companion-navi CLI 대화")
    parser.add_argument(
        "--brain",
        choices=["gemini", "anthropic", "echo"],
        help="config.yaml의 brain.vendor를 이번 실행만 덮어쓴다 (벤더 교체 검증용)",
    )
    args = parser.parse_args()

    config = load_config()
    if args.brain:
        config = replace(config, brain=replace(config.brain, vendor=args.brain))
    asyncio.run(chat(config))


if __name__ == "__main__":
    main()
