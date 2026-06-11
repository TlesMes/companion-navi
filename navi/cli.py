"""Phase 1의 입과 귀 — CLI 텍스트 대화 루프.

실행: python -m navi.cli [--brain gemini|anthropic|echo] [-v | -vv]
종료: /quit 또는 Ctrl+C. 실행마다 새 session_id를 발급하지만
단기기억은 세션 경계 없이 인출하므로 껐다 켜도 직전 대화가 이어진다.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
import uuid
from dataclasses import replace
from pathlib import Path

from navi.brain import create_brain
from navi.conductor import Conductor
from navi.config import Config, load_config
from navi.memory import MemoryStore
from navi.persona import CharacterCard

# __name__ 금지: python -m navi.cli 실행 시 __main__이 되어 navi 로거 계층을 벗어난다
log = logging.getLogger("navi.cli")


def _setup_logging(verbosity: int) -> None:
    """콘솔은 -v 단계에 따라, 파일(logs/navi.log)은 항상 기록.

    데몬화(Phase 3+) 이후엔 화면이 없으므로 파일 로그가 본명이다.
    콘솔 로그는 stderr로 — 대화 출력(stdout)과 섞이지 않게.
    """
    console_level = {0: logging.WARNING, 1: logging.INFO}.get(verbosity, logging.DEBUG)
    file_level = logging.DEBUG if verbosity >= 2 else logging.INFO
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    console = logging.StreamHandler()
    console.setLevel(console_level)
    console.setFormatter(formatter)

    Path("logs").mkdir(exist_ok=True)
    file = logging.FileHandler(Path("logs") / "navi.log", encoding="utf-8")
    file.setLevel(file_level)
    file.setFormatter(formatter)

    root = logging.getLogger("navi")
    root.setLevel(logging.DEBUG)
    root.addHandler(console)
    root.addHandler(file)


async def chat(config: Config) -> None:
    store = MemoryStore(config.db_path)
    card = CharacterCard.load(config.persona_card_path)
    brain = create_brain(config)
    conductor = Conductor(card=card, memory=store, config=config)
    user_id = store.ensure_user(display_name="친구")
    session_id = uuid.uuid4().hex
    log.info("세션 시작 — session=%s, vendor=%s", session_id, config.brain.vendor)

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
            started = time.perf_counter()
            first_token_at: float | None = None
            print(f"{card.character}> ", end="", flush=True)
            try:
                async for token in brain.generate_stream(request):
                    if first_token_at is None:
                        first_token_at = time.perf_counter()
                        # 첫 토큰 레이턴시 — "첫 오디오 ~1초" 예산의 LLM 구간 선행 지표
                        log.info("첫 토큰까지 %.0fms", (first_token_at - started) * 1000)
                    print(token, end="", flush=True)  # 전 구간 스트리밍의 텍스트 버전
                print()
            except Exception:
                print()
                log.exception("두뇌 호출 실패 — 이 턴은 기억에 남기지 않는다")
                print("(…말이 끊겼다. logs/navi.log 참고)")
                continue

            result = brain.last_result
            if result is None:  # 스트림이 결과 없이 끊긴 경우 — 기억에 남기지 않는다
                continue
            store.append_turn(session_id, user_id, role="user", text=text)
            store.append_turn(session_id, user_id, role="assistant", text=result.full_text)
            store.log_usage("llm", result.usage)
            log.info(
                "응답 완료 — %d자, 총 %.0fms, 토큰 in=%d out=%d",
                len(result.full_text),
                (time.perf_counter() - started) * 1000,
                result.usage.input_tokens,
                result.usage.output_tokens,
            )
    finally:
        store.close()
        log.info("세션 종료 — session=%s", session_id)


def main() -> None:
    parser = argparse.ArgumentParser(prog="navi", description="companion-navi CLI 대화")
    parser.add_argument(
        "--brain",
        choices=["gemini", "anthropic", "echo"],
        help="config.yaml의 brain.vendor를 이번 실행만 덮어쓴다 (벤더 교체 검증용)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="-v: 진행 로그(INFO), -vv: 프롬프트 전문까지(DEBUG)",
    )
    args = parser.parse_args()
    _setup_logging(args.verbose)

    config = load_config()
    if args.brain:
        config = replace(config, brain=replace(config.brain, vendor=args.brain))
    asyncio.run(chat(config))


if __name__ == "__main__":
    main()
