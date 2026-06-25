"""검문① 실동 검증 — 마이크로 수면 명령을 말해 STT 출력과 게이트 판정을 눈으로 확인한다.

목적: 텍스트 완전 일치 게이트가 실제 STT 출력과 어긋나는지 본다. 1차 측정(짧은 단어
"자라"·"꺼")은 Whisper가 오인식·환각으로 전멸 → 2차로 **긴 구절** + **문장부호 정규화**가
구제하는지 측정한다. repr로 부호·꼬리말까지 그대로 드러낸다.

실행(.venv-voice 필요):
  .venv-voice/Scripts/python scripts/try/verify_gate.py --mic 1

말할 명령(각각 또렷이, 한 번씩 — 후보 긴 구절):
  이제 그만 잘게 / 나비 이제 잘게 / 오늘은 그만 잘래 / 이제 자러 갈게 / 잘 자 나비 / 그만 자자
대조군(통과해야 정상): 나 오늘 잘 잤어 / 이제 뭐 하지 / 나 이제 자라
Ctrl+C로 종료.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔 한글 출력 방어

# 후보 수면 명령 — 긴·또렷한 구절(Whisper가 문맥으로 잘 받아쓰게)
CANDIDATE_SLEEP = [
    "이제 그만 잘게",
    "나비 이제 잘게",
    "오늘은 그만 잘래",
    "이제 자러 갈게",
    "잘 자 나비",
    "그만 자자",
]

# 정규화: 양끝 문장부호·공백 제거(내부 공백은 단일화). "자라!" "가자." 같은 꼬리 부호 흡수
_EDGE_PUNCT = re.compile(r"^[\s.!?~,…。·\"'\-]+|[\s.!?~,…。·\"'\-]+$")


def normalize(text: str) -> str:
    t = _EDGE_PUNCT.sub("", text)
    return re.sub(r"\s+", " ", t).strip()


_CAND_NORM = {normalize(c) for c in CANDIDATE_SLEEP}


async def _transcribe(stt, utt) -> str:
    session = await stt.open_stream("ko")
    for chunk in utt.chunks:
        await session.feed(chunk)
    result = await session.finalize()
    return result.text


async def run(stt_model: str, mic: int | None, vad_threshold: float | None) -> None:
    from navi.ear import create_vad
    from navi.ear.mic import MicListener
    from navi.stt.fasterwhisper import FasterWhisperStt

    print(f"[후보 명령] {CANDIDATE_SLEEP}")
    print(f"[STT 모델 로딩 중… {stt_model}]", flush=True)
    stt = FasterWhisperStt(model_size=stt_model)
    await asyncio.to_thread(stt.warmup)

    vad = create_vad("energy", threshold=vad_threshold) if vad_threshold else None
    utt_stream = MicListener(vad, device=mic).utterances()
    print("[마이크 듣는 중 — 후보 구절을 또렷이 말하세요. Ctrl+C로 종료]\n", flush=True)

    try:
        while True:
            try:
                utt = await utt_stream.__anext__()
            except (StopAsyncIteration, KeyboardInterrupt):
                break
            t0 = time.perf_counter()
            text = await _transcribe(stt, utt)
            ms = (time.perf_counter() - t0) * 1000
            if not text:
                print("  (인식 결과 없음)")
                continue
            raw_hit = text.strip() in CANDIDATE_SLEEP            # 원문 완전 일치
            norm = normalize(text)
            norm_hit = norm in _CAND_NORM                        # 정규화 후 일치
            hit = raw_hit or norm_hit
            mark = "■ SLEEP" if hit else "· PASS "
            print(f"  {mark}  STT={text!r}  norm={norm!r}  "
                  f"[raw={'O' if raw_hit else 'X'} norm={'O' if norm_hit else 'X'}]  ({ms:.0f}ms)")
    finally:
        print("\n[검증 종료]")


def main() -> None:
    p = argparse.ArgumentParser(description="검문① 실동 검증 (마이크→STT→게이트)")
    p.add_argument("--stt-model", default="large-v3-turbo", help="faster-whisper 모델 (기본 large-v3-turbo, 빠르게=small)")
    p.add_argument("--mic", type=int, default=None, help="입력 장치 번호 (목록: python scripts/mic_check.py)")
    p.add_argument("--vad-threshold", type=float, default=None, help="발화 RMS 임계 (말해도 안 잡히면 ↓)")
    args = p.parse_args()
    try:
        asyncio.run(run(args.stt_model, args.mic, args.vad_threshold))
    except KeyboardInterrupt:
        print("\n[검증 종료]")


if __name__ == "__main__":
    main()
