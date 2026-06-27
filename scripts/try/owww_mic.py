"""openWakeWord 단독 마이크 테스트 — 데몬 없이 KWS 런타임만 검증 (D7 검증2·3).

내장 영어 모델(기본 hey_jarvis)이나 커스텀 .onnx를 로드해 실시간 감지하고, 대기 중 프로세스
CPU 점유율을 주기 출력한다(검증2: <2% 눈으로 확인). predict는 로컬 onnxruntime이라 최초 모델
다운로드 후 네트워크 0(검증3). 데몬과 독립이라 openWakeWord만 떼어 자원·동작을 본다.

실행(.venv-voice):
  python scripts/try/owww_mic.py [--model hey_jarvis | --model secrets/navi_ko.onnx] [--mic 1] [--threshold 0.5]
"""

from __future__ import annotations

import argparse
import queue
import time


def main() -> None:
    ap = argparse.ArgumentParser(description="openWakeWord 단독 마이크 테스트")
    ap.add_argument("--model", default="hey_jarvis", help="내장 모델 이름 또는 .onnx 경로")
    ap.add_argument("--mic", type=int, default=None, help="입력 장치 번호(목록: scripts/mic_check.py)")
    ap.add_argument("--threshold", type=float, default=0.5, help="감지 임계 0~1")
    ap.add_argument("--cpu-interval", type=float, default=2.0, help="CPU%% 출력 주기(초)")
    args = ap.parse_args()

    import numpy as np
    import openwakeword
    import psutil
    import sounddevice as sd
    from openwakeword.model import Model

    sr, frame = 16000, 1280  # 16kHz · 80ms

    print("[특징모델 준비 중…]", flush=True)
    openwakeword.utils.download_models()  # 있으면 즉시 통과(오프라인 안전)
    model = Model(wakeword_models=[args.model], inference_framework="onnx")
    print(f"[모델 로드: {args.model} | 임계 {args.threshold}]", flush=True)

    frames: queue.Queue[bytes] = queue.Queue()

    def on_frame(indata, _frames, _time, status) -> None:
        if status:
            print(f"[stream status: {status}]", flush=True)
        frames.put(bytes(indata))  # 콜백은 큐에 넣기만 — predict로 PortAudio를 막지 않는다

    proc = psutil.Process()
    proc.cpu_percent(None)  # 기준점(첫 호출은 0.0 반환)
    last_cpu = time.monotonic()

    stream = sd.RawInputStream(
        samplerate=sr, channels=1, dtype="int16", blocksize=frame,
        device=args.mic, callback=on_frame,
    )
    print("[듣는 중 — 호출어를 말하세요. Ctrl+C로 종료]", flush=True)
    try:
        with stream:  # 정상·예외·Ctrl+C 어느 경로로 나가도 스트림을 닫는다(자원 누수 방지)
            while True:
                samples = np.frombuffer(frames.get(), dtype=np.int16)
                scores = model.predict(samples)
                best_name = max(scores, key=scores.get)
                best = scores[best_name]
                if best >= args.threshold:
                    print(
                        f"  ★ DETECT {best_name} ({best:.3f}) @ {time.strftime('%H:%M:%S')}",
                        flush=True,
                    )
                    model.reset()  # 같은 발화로 다음 프레임에 재감지 방지
                now = time.monotonic()
                if now - last_cpu >= args.cpu_interval:
                    print(f"  … 대기 | CPU {proc.cpu_percent(None):.1f}% | 최고점수 {best:.3f}", flush=True)
                    last_cpu = now
    except KeyboardInterrupt:
        print("\n[종료]")


if __name__ == "__main__":
    main()
