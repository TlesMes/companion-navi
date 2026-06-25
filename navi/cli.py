"""Phase 1의 입과 귀 — CLI 텍스트 대화 루프.

실행: python -m navi.cli [--brain ...] [--mouth ...] [--persona 이름] [--voice] [--listen] [--input WAV] [-v | -vv]
종료: /quit 또는 Ctrl+C. 실행마다 새 session_id를 발급하지만
단기기억은 세션 경계 없이 인출하므로 껐다 켜도 직전 대화가 이어진다.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
import uuid
from dataclasses import replace
from pathlib import Path

from navi.brain import create_brain
from navi.conductor import Conductor
from navi.config import Config, load_config
from navi.memory import MemoryStore
from navi.models import AudioChunk
from navi.mouth import create_mouth
from navi.persona import CharacterCard
from navi.pipeline import TurnPipeline
from navi.stt import create_stt

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


async def _transcribe_file(path: Path) -> str:
    """음성 파일(wav/m4a/mp3 등)을 faster-whisper로 받아쓴다.

    faster-whisper는 파일 경로를 직접 받아 av 라이브러리로 디코딩하므로
    WAV 변환 없이 m4a 등도 그대로 넘긴다.
    """
    from navi.stt.fasterwhisper import FasterWhisperStt

    stt = FasterWhisperStt()
    text, _ = await asyncio.to_thread(stt._transcribe_path, str(path), "ko")
    return text


async def _transcribe_utterance(stt, utt) -> str:
    """마이크 발화 1건(PCM 프레임 묶음)을 STT 스트리밍 세션으로 받아쓴다 (계약 4.3)."""
    session = await stt.open_stream("ko")
    for chunk in utt.chunks:
        await session.feed(chunk)
    result = await session.finalize()
    return result.text


async def chat(
    config: Config,
    *,
    use_voice: bool = False,
    input_wav: Path | None = None,
    listen: bool = False,
    mic_device: int | None = None,
    vad_threshold: float | None = None,
    stt_model: str = "large-v3-turbo",
) -> None:
    store = MemoryStore(config.db_path)
    card = CharacterCard.load(config.persona_card_path)
    brain = create_brain(config)
    conductor = Conductor(card=card, memory=store, config=config)
    user_id = store.ensure_user(display_name="친구")
    session_id = uuid.uuid4().hex
    log.info("세션 시작 — session=%s, vendor=%s", session_id, config.brain.vendor)

    # 음성 모드: Brain 토큰을 Mouth로 흘려 나비가 음성으로 답한다(텍스트는 화면에 동시 echo).
    # 텍스트 모드(기본)는 기존 print 경로 그대로 — 음성 의존성 없이 가볍게.
    pipeline: TurnPipeline | None = None
    if use_voice:
        mouth = create_mouth(config.mouth.vendor, **config.mouth.options)
        pipeline = TurnPipeline(
            brain=brain, mouth=mouth, conductor=conductor, voice=config.mouth.voice
        )
        log.info(
            "음성 모드 — mouth=%s, voice=%s", config.mouth.vendor, config.mouth.voice.name
        )

    voice_note = f" 목소리: {config.mouth.vendor}." if use_voice else ""
    print(
        f"{card.character} 깨어남 — 두뇌: {config.brain.vendor}({config.brain.model})."
        f"{voice_note} /quit 으로 종료."
    )
    wav_mode = input_wav is not None  # WAV 모드면 1턴 후 종료

    # 마이크 실시간 모드: Ear(VAD 엔드포인팅)가 발화 1건씩 내보내면 STT로 받아쓴다.
    # STT 모델은 한 번만 로드해 발화마다 재사용한다.
    utt_stream = None
    listen_stt = None
    if listen:
        from navi.ear import create_vad
        from navi.ear.mic import MicListener
        from navi.stt.fasterwhisper import FasterWhisperStt

        listen_stt = FasterWhisperStt(model_size=stt_model)
        print(f"[STT 모델 로딩 중… {stt_model}]", flush=True)
        await asyncio.to_thread(listen_stt.warmup)
        vad = create_vad("energy", threshold=vad_threshold) if vad_threshold else None
        utt_stream = MicListener(vad, device=mic_device).utterances()
        print("[마이크 듣는 중 — 말하면 나비가 답합니다. Ctrl+C로 종료]", flush=True)

    try:
        while True:
            # ── 입력 획득 ──────────────────────────────────────────────────
            if input_wav is not None:
                print(f"[STT] {input_wav.name} 받아쓰는 중…")
                text = await _transcribe_file(input_wav)
                input_wav = None
                if not text:
                    print("[STT] 인식 결과 없음")
                    break
                print(f"나> {text}")
            elif utt_stream is not None:
                try:
                    utt = await utt_stream.__anext__()
                except (StopAsyncIteration, KeyboardInterrupt):
                    print()
                    break
                print("[받아쓰는 중…]")
                stt_t0 = time.perf_counter()
                text = await _transcribe_utterance(listen_stt, utt)
                log.info("STT %.0fms", (time.perf_counter() - stt_t0) * 1000)
                if not text:
                    print("[인식 결과 없음 — 다시 말하세요]")
                    continue
                print(f"나> {text}")
            else:
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

            # ── Brain(→Mouth) 처리 ─────────────────────────────────────────
            started = time.perf_counter()
            first_token_at: float | None = None
            print(f"{card.character}> ", end="", flush=True)

            def _echo(token: str) -> None:
                nonlocal first_token_at
                if first_token_at is None:
                    first_token_at = time.perf_counter()
                    log.info("첫 토큰까지 %.0fms", (first_token_at - started) * 1000)
                print(token, end="", flush=True)

            try:
                if pipeline is not None:
                    result = await pipeline.run_turn(
                        text, user_id=user_id, session_id=session_id, echo=_echo
                    )
                else:
                    request = conductor.build_request(
                        text, user_id=user_id, session_id=session_id
                    )
                    async for token in brain.generate_stream(request):
                        _echo(token)
                    result = brain.last_result
                print()
            except Exception:
                print()
                log.exception("두뇌 호출 실패 — 이 턴은 기억에 남기지 않는다")
                print("(…말이 끊겼다. logs/navi.log 참고)")
                if wav_mode:
                    break
                continue
            if result is None:
                if wav_mode:
                    break
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
            if wav_mode:
                break  # WAV 1턴 처리 완료 → 종료
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
    parser.add_argument(
        "--db",
        help="기억 DB 경로 덮어쓰기 — 본 기억을 오염시키지 않는 임시 DB 테스트용",
    )
    parser.add_argument(
        "--voice",
        action="store_true",
        help="음성 모드 — 나비가 config.yaml의 mouth로 음성 답변(.venv-voice 필요)",
    )
    parser.add_argument(
        "--input",
        metavar="WAV",
        help="WAV 파일을 STT로 받아쓴 뒤 Brain(→Mouth)까지 1턴 처리하고 종료. --voice와 함께 쓰면 전 구간 검증 가능.",
    )
    parser.add_argument(
        "--listen",
        action="store_true",
        help="마이크 실시간 모드 — VAD로 발화 종료를 감지해 STT→Brain(→Mouth) 루프. Ctrl+C로 종료. (.venv-voice 필요)",
    )
    parser.add_argument(
        "--mic",
        type=int,
        metavar="INDEX",
        help="입력 장치 번호 지정 (기본 마이크가 가상 장치일 때). 목록: python scripts/mic_check.py",
    )
    parser.add_argument(
        "--vad-threshold",
        type=float,
        metavar="RMS",
        help="발화로 인정할 RMS 임계 (기본 150). 말해도 안 잡히면 ↓, 잡음에 반응하면 ↑. mic_check로 발화 RMS 확인.",
    )
    parser.add_argument(
        "--stt-model",
        default="large-v3-turbo",
        metavar="SIZE",
        help="faster-whisper 모델 크기 (기본 large-v3-turbo). 속도 우선이면 small 또는 base.",
    )
    parser.add_argument(
      "--mouth",
        choices=["fake", "supertonic", "gptsovits"],
        help="config.yaml의 mouth.vendor를 이번 실행만 덮어쓴다 (음성 벤더 교체 검증용, --voice와 함께)",
    )
    parser.add_argument(
        "--persona",
        help="페르소나를 이번 실행만 교체 — 이름만(personas/<이름>.yaml). 예: --persona aris",
    )
    args = parser.parse_args()
    _setup_logging(args.verbose)

    persona_card = f"personas/{args.persona}.yaml" if args.persona else None
    config = load_config(mouth_vendor=args.mouth, persona_card=persona_card)
    if args.brain:
        config = replace(config, brain=replace(config.brain, vendor=args.brain))
    if args.db:
        config = replace(config, db_path=Path(args.db))
    input_wav = Path(args.input) if args.input else None
    try:
        asyncio.run(
            chat(
                config,
                use_voice=args.voice,
                input_wav=input_wav,
                listen=args.listen,
                mic_device=args.mic,
                vad_threshold=args.vad_threshold,
                stt_model=args.stt_model,
            )
        )
    except KeyboardInterrupt:
        # 스트리밍 중 Ctrl+C — 턴은 즉시 커밋되므로 데이터는 안전, traceback만 숨긴다
        print("\n(나비가 잠들었다)")
    finally:
        if args.voice:
            # 음성 모드에서만: GPT-SoVITS 합성 스레드·PortAudio·torch 잔여 스레드가
            # asyncio shutdown_default_executor 의 join 대기를 막아 프리즈한다.
            # 기억(DB)은 chat() finally에서 이미 close됐으므로 즉시 종료해도 안전.
            os._exit(0)


if __name__ == "__main__":
    main()
