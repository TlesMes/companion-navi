"""로컬 STT 받아쓰기 검증용 — 음성 파일을 텍스트로 옮기고 한국어 정확도·소요시간을 본다 (D2 도구).

faster-whisper(CTranslate2) 기반. 같은 모델 가중치면 받아쓴 텍스트는 런타임·하드웨어와
무관하게 동일하다 — 그래서 품질(CER) 판단은 여기 CPU에서 해도 데스크톱 배포와 같은 결과다.
GPU 가속(AMD=Vulkan/DirectML, NVIDIA=CUDA)은 레이턴시만 바꾼다 → 배포 단계 과제.

사용 예:
  python scripts/try/try_stt.py 발화.wav                              # 기본 large-v3-turbo
  python scripts/try/try_stt.py 발화.wav --models large-v3-turbo large-v3   # 두 모델 품질 비교
  python scripts/try/try_stt.py 발화.wav --device cuda                # 1050 Ti 노트북이면 CUDA

품질 위주 비교라 여러 모델을 한 번에 돌려 받아쓴 텍스트를 나란히 본다. 정답(실제 발화)과
대조해 어느 모델이 한국어를 잘 받아쓰는지 눈으로 판단한다. CER은 직접 세거나 VITO 웹 데모
같은 기준점과 견준다.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔(cp949)에서 한글·기호 출력 깨짐·크래시 방지


def main() -> None:
    parser = argparse.ArgumentParser(description="faster-whisper 로컬 한국어 받아쓰기·비교")
    parser.add_argument("audio", help="받아쓸 음성 파일 (wav/mp3/m4a 등)")
    parser.add_argument(
        "--models",
        nargs="+",
        default=["large-v3-turbo"],
        help="비교할 whisper 모델들 (예: large-v3-turbo large-v3 medium)",
    )
    parser.add_argument("--device", default="cpu", help="cpu | cuda (AMD GPU는 미지원 — Vulkan 경로 별도)")
    parser.add_argument("--compute", default="int8", help="int8 | float16 | float32")
    args = parser.parse_args()

    if not Path(args.audio).exists():
        raise SystemExit(f"파일이 없습니다: {args.audio}")

    from dotenv import load_dotenv  # .env의 HF_TOKEN → 모델 다운로드 속도제한 해제

    load_dotenv()
    from faster_whisper import WhisperModel  # 무거운 import는 뒤로

    print(f"[음성] {args.audio}\n")
    results: list[tuple[str, str]] = []
    for name in args.models:
        t0 = time.time()
        model = WhisperModel(name, device=args.device, compute_type=args.compute)
        load_s = time.time() - t0

        t1 = time.time()
        segments, info = model.transcribe(args.audio, language="ko", beam_size=5)
        text = "".join(seg.text for seg in segments).strip()  # 소진 시점에 실제 추론
        infer_s = time.time() - t1

        print(f"[{name}] 로드 {load_s:.1f}s · 받아쓰기 {infer_s:.2f}s "
              f"(음성 {info.duration:.1f}s, RTF {infer_s / info.duration:.2f}, "
              f"lang {info.language} p={info.language_probability:.2f})")
        print(f"  → {text}\n")
        results.append((name, text))

    # 모델이 둘 이상이면 한눈에 다시 모아 보여준다(정답과 대조하기 쉽게)
    if len(results) > 1:
        print("=== 비교 (정답과 대조) ===")
        for name, text in results:
            print(f"[{name}] {text}")


if __name__ == "__main__":
    main()
