"""로컬 STT 받아쓰기 검증용 — 음성 파일을 텍스트로 옮기고 한국어 정확도·소요시간을 본다 (D2 도구).

faster-whisper(CTranslate2) 기반. 같은 모델 가중치면 받아쓴 텍스트는 런타임·하드웨어와
무관하게 동일하다 — 그래서 품질(CER) 판단은 여기 CPU에서 해도 데스크톱 배포와 같은 결과다.
GPU 가속(AMD=Vulkan/DirectML, NVIDIA=CUDA)은 레이턴시만 바꾼다 → 배포 단계 과제.

사용 예:
  python scripts/try_stt.py scripts/out/tts_M1.wav
  python scripts/try_stt.py 발화.wav --model large-v3   # 풀모델(8GB GPU/넉넉한 CPU)
  python scripts/try_stt.py 발화.wav --device cuda       # 1050 Ti 노트북이면 CUDA

기본 모델은 large-v3-turbo(4GB VRAM·CPU에서도 OK, 품질은 풀모델과 거의 동급).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔(cp949)에서 한글·기호 출력 깨짐·크래시 방지


def main() -> None:
    parser = argparse.ArgumentParser(description="faster-whisper 로컬 한국어 받아쓰기")
    parser.add_argument("audio", help="받아쓸 음성 파일 (wav/mp3/m4a 등)")
    parser.add_argument("--model", default="large-v3-turbo", help="whisper 모델 크기")
    parser.add_argument("--device", default="cpu", help="cpu | cuda (AMD GPU는 미지원 — Vulkan 경로 별도)")
    parser.add_argument("--compute", default="int8", help="int8 | float16 | float32")
    args = parser.parse_args()

    if not Path(args.audio).exists():
        raise SystemExit(f"파일이 없습니다: {args.audio}")

    from dotenv import load_dotenv  # .env의 HF_TOKEN → 모델 다운로드 속도제한 해제

    load_dotenv()
    from faster_whisper import WhisperModel  # 무거운 import는 뒤로

    t0 = time.time()
    model = WhisperModel(args.model, device=args.device, compute_type=args.compute)
    print(f"[모델 로드] {args.model} on {args.device}/{args.compute} — {time.time() - t0:.1f}s")

    t1 = time.time()
    segments, info = model.transcribe(args.audio, language="ko", beam_size=5)
    text = "".join(seg.text for seg in segments)  # 제너레이터 소진 시점에 실제 추론
    elapsed = time.time() - t1

    print(f"[받아쓰기] {elapsed:.2f}s (음성 {info.duration:.1f}s, RTF {elapsed / info.duration:.2f})")
    print(f"[lang] {info.language} (p={info.language_probability:.2f})")
    print(f"[텍스트] {text.strip()}")


if __name__ == "__main__":
    main()
