"""로컬 TTS 청취 비교용 — 한국어 대사를 합성해 wav로 저장하고 소요시간을 잰다 (D3 결정 도구).

벤더 결정 전 실험 스크립트라 navi 패키지에 넣지 않는다. 음질·음색은 귀로 정한다(원칙).

사용 예:
  python scripts/try_tts.py                         # 기본 대사·기본 음색
  python scripts/try_tts.py --voice F1 --text "오늘 좀 피곤하네, 일찍 잘래"
  python scripts/try_tts.py --voices M1 M2 F1 F2     # 여러 음색을 한 번에 비교

생성물은 scripts/out/ 아래 voice별 wav. 직접 들어보고 나비의 목소리 후보를 고른다.
"""

from __future__ import annotations

import argparse
import sys
import time
import wave
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔(cp949)에서 한글·기호 출력 깨짐·크래시 방지

DEFAULT_LINE = "안녕, 잘 잤어? 오늘은 좀 늦게 일어났네. 날씨 좋으니까 이따 산책이라도 할까?"
OUT_DIR = Path(__file__).parent / "out"


def main() -> None:
    parser = argparse.ArgumentParser(description="Supertonic 로컬 한국어 TTS 청취 비교")
    parser.add_argument("--text", default=DEFAULT_LINE, help="합성할 한국어 대사")
    parser.add_argument("--voice", default="F1", help="단일 음색 이름 (나비 잠정 기본값 F1)")
    parser.add_argument("--voices", nargs="+", help="여러 음색을 한 번에 (--voice 무시)")
    parser.add_argument("--steps", type=int, default=8, help="디퓨전 스텝(품질↔속도)")
    parser.add_argument("--speed", type=float, default=1.05, help="말 속도")
    args = parser.parse_args()

    from dotenv import load_dotenv  # .env의 HF_TOKEN → 모델 다운로드 속도제한 해제

    load_dotenv()
    from supertonic import TTS  # 무거운 import는 인자 파싱 뒤로

    t0 = time.time()
    tts = TTS(auto_download=True)
    print(f"[모델 로드] {time.time() - t0:.1f}s")

    OUT_DIR.mkdir(exist_ok=True)
    voices = args.voices or [args.voice]
    for voice in voices:
        try:
            style = tts.get_voice_style(voice_name=voice)
        except Exception as e:  # 잘못된 음색 이름이면 건너뛰되 멈추지 않는다
            print(f"[{voice}] 음색 로드 실패: {e}")
            continue
        t1 = time.time()
        wav, _ = tts.synthesize(
            text=args.text,
            voice_style=style,
            lang="ko",
            total_steps=args.steps,
            speed=args.speed,
        )
        synth_s = time.time() - t1
        out = OUT_DIR / f"tts_{voice}.wav"
        tts.save_audio(wav, str(out))
        # 실제 음성 길이는 저장된 wav에서 읽는다(샘플레이트 가정 없이 정확)
        with wave.open(str(out)) as w:
            audio_s = w.getnframes() / w.getframerate()
        # RTF = 합성시간 / 음성길이 — 1보다 작으면 실시간보다 빠르게 합성
        print(f"[{voice}] 합성 {synth_s:.2f}s / 음성 {audio_s:.1f}s "
              f"(RTF {synth_s / audio_s:.2f}) → {out}")


if __name__ == "__main__":
    main()
