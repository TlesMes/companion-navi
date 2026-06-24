"""마이크 진단 — 입력 장치 목록 + 실시간 RMS 미터.

어느 장치가 내 목소리를 받는지, 발화 시 RMS가 얼마인지(EnergyVad threshold 튜닝)를 눈으로 본다.
실행: python scripts/mic_check.py            # 장치 목록만
      python scripts/mic_check.py <index>    # 그 장치로 실시간 RMS (말해보기)
종료: Ctrl+C
"""
import array
import math
import sys
import time

import sounddevice as sd

SR = 16000
FRAME = 320  # 20ms

if len(sys.argv) < 2:
    print(sd.query_devices())
    print(f"\n기본 입력 장치: {sd.default.device[0]}")
    print("\n위 목록에서 '... (N in, ...)'으로 in>0인 장치 번호를 골라:")
    print("  python scripts/mic_check.py <번호>")
    sys.exit(0)

dev = int(sys.argv[1])
info = sd.query_devices(dev)
print(f"장치 {dev}: {info['name']} — 말해보세요. 막대가 움직이면 그 마이크가 잡힙니다. (Ctrl+C 종료)\n")


def _cb(indata, frames, t, status):
    samples = array.array("h")
    samples.frombytes(bytes(indata))
    rms = math.sqrt(sum(s * s for s in samples) / len(samples)) if samples else 0.0
    bar = "#" * min(50, int(rms / 100))
    print(f"\rRMS {rms:6.0f} |{bar:<50}|", end="", flush=True)


with sd.RawInputStream(
    samplerate=SR, channels=1, dtype="int16", blocksize=FRAME, device=dev, callback=_cb
):
    while True:
        time.sleep(0.1)
