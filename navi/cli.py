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
from navi.gatekeeper import GateResult, check_gate
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
    wakeword: bool = False,
    mic_device: int | None = None,
    vad_threshold: float | None = None,
    stt_model: str = "large-v3-turbo",
    active_timeout_ms: int | None = None,
) -> None:
    store = MemoryStore(config.db_path)
    card = CharacterCard.load(config.persona_card_path)
    brain = create_brain(config)
    conductor = Conductor(card=card, memory=store, config=config)
    user_id = store.ensure_user(display_name="친구")
    session_id = uuid.uuid4().hex
    log.info("세션 시작 — session=%s, vendor=%s", session_id, config.brain.vendor)

    # 웨이크워드를 켜려면 엔진 설정이 갖춰져야 한다 — 없으면 일찍 안내하고 끝낸다(데몬은 정상).
    if wakeword and not config.wakeword.ready:
        print(
            "[웨이크워드 설정 미비 — config.yaml ear.wakeword 확인 "
            "(openwakeword: model_name 또는 model_path / vosk: 모델+호출어 / porcupine: 키+키워드)]"
        )
        store.close()
        return

    # 음성 모드: Brain 토큰을 Mouth로 흘려 나비가 음성으로 답한다(텍스트는 화면에 동시 echo).
    # 텍스트 모드(기본)는 기존 print 경로 그대로 — 음성 의존성 없이 가볍게.
    pipeline: TurnPipeline | None = None
    if use_voice:
        mouth = create_mouth(config.mouth.vendor, **config.mouth.options)
        print(f"[TTS 엔진 로딩 중… {config.mouth.vendor}]", flush=True)
        await asyncio.to_thread(mouth.warmup)
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

    # ── 한 턴 처리: 입력 텍스트 → Brain(→Mouth) → 기억. 모든 입력 경로가 공유한다. ──
    async def _run_turn(text: str) -> None:
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
            return
        if result is None:
            return
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

    # 마이크 실시간 모드: STT 모델은 한 번만 로드해 발화마다 재사용한다.
    listen_stt = None
    if listen:
        from navi.stt.fasterwhisper import FasterWhisperStt

        listen_stt = FasterWhisperStt(model_size=stt_model)
        print(f"[STT 모델 로딩 중… {stt_model}]", flush=True)
        await asyncio.to_thread(listen_stt.warmup)

    try:
        if listen and wakeword:
            await _listen_wakeword(
                config,
                listen_stt,
                _run_turn,
                mic_device=mic_device,
                vad_threshold=vad_threshold,
                active_timeout_ms=active_timeout_ms,
            )
        else:
            await _input_loop(
                _run_turn,
                input_wav=input_wav,
                listen=listen,
                wav_mode=wav_mode,
                listen_stt=listen_stt,
                mic_device=mic_device,
                vad_threshold=vad_threshold,
            )
    finally:
        store.close()
        log.info("세션 종료 — session=%s", session_id)


async def _input_loop(
    run_turn,
    *,
    input_wav: Path | None,
    listen: bool,
    wav_mode: bool,
    listen_stt,
    mic_device: int | None,
    vad_threshold: float | None,
) -> None:
    """텍스트 / WAV 1턴 / 상시청취(--listen 단독, 웨이크워드 없음) 입력 루프.

    상시청취는 하위호환 경로 — 마이크를 열면 항상 STT를 돌린다. 청취축(웨이크워드로만 열림)은
    _listen_wakeword가 담당한다. 검문① SLEEP은 여기선 복귀할 세션이 없어 루프를 종료한다.
    """
    utt_stream = None
    if listen:
        from navi.ear import create_vad
        from navi.ear.mic import MicListener

        vad = create_vad("energy", threshold=vad_threshold) if vad_threshold else None
        utt_stream = MicListener(vad, device=mic_device).utterances()
        print("[마이크 듣는 중 — 말하면 나비가 답합니다. Ctrl+C로 종료]", flush=True)

    while True:
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
            if check_gate(text) == GateResult.SLEEP:
                print("(나비가 잠들었다. Ctrl+C로 완전 종료)")
                log.info("검문① SLEEP — %r", text)
                break
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

        await run_turn(text)
        if wav_mode:
            break  # WAV 1턴 처리 완료 → 종료


def _build_wakeword(cfg):
    """WakeWordConfig → WakeWord 어댑터. engine 한 줄로 엔진을 가른다(벤더 종속 금지)."""
    from navi.ear import create_wakeword

    if cfg.engine == "openwakeword":
        return create_wakeword(
            "openwakeword",
            model_path=cfg.owww_model_path,
            model_name=cfg.owww_model_name,
            threshold=cfg.threshold,
        )
    if cfg.engine == "vosk":
        return create_wakeword("vosk", model_path=cfg.vosk_model_path, keywords=cfg.keywords)
    if cfg.engine == "porcupine":
        return create_wakeword(
            "porcupine",
            access_key=cfg.access_key,
            keyword_path=cfg.keyword_path,
            model_path=cfg.model_path,
            sensitivity=cfg.sensitivity,
        )
    raise ValueError(
        f"알 수 없는 wakeword engine: {cfg.engine!r} (openwakeword | vosk | porcupine)"
    )


async def _listen_wakeword(
    config: Config,
    listen_stt,
    run_turn,
    *,
    mic_device: int | None,
    vad_threshold: float | None,
    active_timeout_ms: int | None,
) -> None:
    """청취축 상태머신(D16): SLEEP=호출어만 청취, ACTIVE=대화 세션(무음 타임아웃까지).

    ListenSession이 이벤트(WAKE/UTTERANCE/SLEEP)를 흘리면 여기서 STT·검문①·Brain을 잇는다.
    검문① SLEEP은 루프 종료가 아니라 session.request_sleep() — ACTIVE만 닫고 호출어 대기로 돌아간다.
    """
    from navi.ear import (
        EventKind,
        ListenSession,
        SleepReason,
        create_vad,
    )
    from navi.ear.mic import MicListener

    try:
        wakeword = _build_wakeword(config.wakeword)
    except ImportError:
        print(
            f"[{config.wakeword.engine} 엔진 미설치 — .venv-voice에 설치하세요 "
            "(vosk: pip install vosk / porcupine: pip install pvporcupine)]"
        )
        return
    vad = create_vad("energy", threshold=vad_threshold) if vad_threshold else None
    session = ListenSession(
        wakeword,
        vad=vad,
        active_timeout_ms=active_timeout_ms or config.wakeword.active_timeout_ms,
    )
    mic = MicListener(
        vad,
        device=mic_device,
        sample_rate=session.sample_rate,
        frame_ms=session.frame_ms,
    )
    print("[잠든 채 호출어를 기다립니다 — 깨우면 대화. Ctrl+C로 종료]", flush=True)

    try:
        async for ev in session.run(mic.frames()):
            if ev.kind == EventKind.WAKE:
                print("[나비가 깨어났습니다 — 말하세요]", flush=True)
            elif ev.kind == EventKind.SLEEP:
                if ev.reason == SleepReason.TIMEOUT:
                    print("[조용해서 다시 잠듭니다]", flush=True)
                else:
                    print("[나비가 잠들었습니다 — 부르면 깨어납니다]", flush=True)
            elif ev.kind == EventKind.UTTERANCE:
                print("[받아쓰는 중…]")
                stt_t0 = time.perf_counter()
                text = await _transcribe_utterance(listen_stt, ev.utterance)
                log.info("STT %.0fms", (time.perf_counter() - stt_t0) * 1000)
                if not text:
                    print("[인식 결과 없음 — 다시 말하세요]")
                    continue
                print(f"나> {text}")
                # 검문① — 수면 명령이면 ACTIVE만 닫고 호출어 대기로(루프 종료 아님)
                if check_gate(text) == GateResult.SLEEP:
                    log.info("검문① SLEEP — %r", text)
                    session.request_sleep()
                    continue
                await run_turn(text)
    finally:
        wakeword.close()


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
        "--wakeword",
        action="store_true",
        help="청취축 켜기(D7) — 평소 SLEEP(STT 끔, 호출어만), 부르면 ACTIVE로 대화. --listen과 함께. "
        "키 필요: .env PICOVOICE_ACCESS_KEY + config.yaml ear.wakeword.keyword_path",
    )
    parser.add_argument(
        "--active-timeout",
        type=float,
        metavar="SEC",
        help="웨이크워드 ACTIVE 유지 시간 — 이만큼 무음이면 다시 SLEEP (기본 config.yaml 30초)",
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
    active_timeout_ms = (
        int(args.active_timeout * 1000) if args.active_timeout is not None else None
    )
    try:
        asyncio.run(
            chat(
                config,
                use_voice=args.voice,
                input_wav=input_wav,
                listen=args.listen,
                wakeword=args.wakeword,
                mic_device=args.mic,
                vad_threshold=args.vad_threshold,
                stt_model=args.stt_model,
                active_timeout_ms=active_timeout_ms,
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
