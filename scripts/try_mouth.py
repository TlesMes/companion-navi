"""SupertonicMouth 실청취 — 대사를 토큰 스트림처럼 흘려 합성·재생하고 첫 오디오 지연을 잰다.

try_tts.py가 '음색'을 고르는 도구라면, 이건 '스트리밍 파이프라인'을 검증하는 도구다:
토큰이 들어오는 대로 문장 경계에서 끊어 첫 문장부터 말하기 시작하는지, 첫 오디오가
~1초 안에 나오는지(설계 원칙 4), 말 끊기(stop)가 즉시 듣는지를 귀와 시계로 확인한다.

스피커가 필요하다. 음성 의존성 설치: pip install -e ".[voice]"

사용 예:
  python scripts/try_mouth.py
  python scripts/try_mouth.py --voice F1 --text "오늘 좀 피곤하네. 일찍 잘까? 내일 보자."
  python scripts/try_mouth.py --barge-in 1.5   # 1.5초 뒤 stop() — 말 끊기 확인
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔(cp949) 한글 출력 깨짐 방지

DEFAULT_LINE = (
    "안녕, 잘 잤어? 오늘은 좀 늦게 일어났네. 날씨 좋으니까 이따 산책이라도 할까?"
)


async def _token_stream(text: str, chunk: int, delay: float):
    """LLM 스트리밍 흉내 — text를 chunk글자씩, delay초 간격으로 흘린다."""
    for i in range(0, len(text), chunk):
        yield text[i : i + chunk]
        await asyncio.sleep(delay)


async def main() -> None:
    parser = argparse.ArgumentParser(description="SupertonicMouth 스트리밍 실청취")
    parser.add_argument("--text", default=DEFAULT_LINE, help="합성할 한국어 대사")
    parser.add_argument("--voice", default="F1", help="Supertonic 음색(=vendor_voice_id)")
    parser.add_argument("--speed", type=float, default=1.05, help="말 속도")
    parser.add_argument("--steps", type=int, default=8, help="디퓨전 스텝(품질↔속도)")
    parser.add_argument("--chunk", type=int, default=3, help="토큰 청크 글자 수")
    parser.add_argument("--delay", type=float, default=0.05, help="토큰 간 간격(초)")
    parser.add_argument(
        "--barge-in", type=float, default=0.0,
        help="N초 뒤 stop() 호출 — 말 끊기 확인(0이면 안 함)",
    )
    args = parser.parse_args()

    from dotenv import load_dotenv  # HF_TOKEN → 모델 다운로드 속도제한 해제

    load_dotenv()

    from navi.models import VoiceProfile
    from navi.mouth.supertonic import SupertonicMouth

    voice = VoiceProfile(name="navi", vendor_voice_id=args.voice, speed=args.speed)
    mouth = SupertonicMouth(total_steps=args.steps)

    # 엔진을 미리 로드해 두면 첫 오디오 지연 측정에서 '모델 로드'를 분리할 수 있다
    t0 = time.time()
    await asyncio.to_thread(mouth._ensure_engine)
    print(f"[엔진 로드] {time.time() - t0:.1f}s")

    # 첫 오디오 지연 측정 — _play 첫 호출 시각을 가로채 기록한 뒤 실제 재생으로 넘긴다
    real_play = mouth._play
    first_audio: list[float] = []
    speak_t0 = 0.0

    def _timed_play(wav):
        if not first_audio:
            first_audio.append(time.time() - speak_t0)
            print(f"[첫 오디오] 발화 시작 후 {first_audio[0]:.2f}s")
        real_play(wav)

    mouth._play = _timed_play

    # barge-in 테스트: N초 뒤 stop() 호출
    if args.barge_in > 0:
        async def _interrupt():
            await asyncio.sleep(args.barge_in)
            print(f"[barge-in] {args.barge_in}s 경과 → stop()")
            mouth.stop()

        asyncio.create_task(_interrupt())

    print(f"[발화] {args.text!r}")
    speak_t0 = time.time()
    await mouth.speak_stream(_token_stream(args.text, args.chunk, args.delay), voice)
    print(f"[완료] 총 {time.time() - speak_t0:.2f}s")


if __name__ == "__main__":
    asyncio.run(main())
