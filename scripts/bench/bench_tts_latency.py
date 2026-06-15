"""Stage 2 — TTS 추론 속도 실측 (TTFA · RTF · VRAM).

Stage 1 음색 합격 모델의 추론 지연을 정량 측정.
웜업 1회 제외 후 ITERATIONS회 반복, 평균 + p95 기록.

사용:
  python scripts/bench/bench_tts_latency.py --model f5tts \\
      --reference ref.wav --ref-text "레퍼런스 전사"

합격 기준 (Stage 2):
  웜 TTFA p95 ≤ 1.5초 (목표 ~1초 / Supertonic 실측 0.6초가 기준선)
  VRAM ≤ 8GB

지표 설명:
  TTFA  — 추론 시작~첫 오디오 출력까지 (배치 엔진은 전체 생성 시간)
  RTF   — 합성 시간 / 오디오 길이 (1 미만 = 실시간 가능)
  VRAM  — 추론 중 GPU 점유 MB
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

ITERATIONS = 10
VRAM_LIMIT_MB = 8_192  # 8 GB

_TEST_SENTENCES: dict[str, str] = {
    "short":  "응, 그래.",
    "medium": "오늘 날씨가 정말 좋지 않아? 기분이 좋아지는 것 같아.",
    "long": (
        "있잖아, 나 오늘 네 생각 많이 했어. 그냥 갑자기 네가 어떻게 지내나 궁금해서. "
        "요즘 바쁜 것 같던데, 잘 쉬고 있어? 밥은 잘 먹고 있고?"
    ),
}


def get_vram_mb() -> float:
    try:
        import torch
        return torch.cuda.memory_allocated(0) / 1e6
    except Exception:
        return 0.0


# ── 모델별 단일 추론 (TTFA, RTF, VRAM 반환) ────────────────────────────────


def _once_f5tts(
    model: object, ref_path: Path, ref_text: str, text: str
) -> tuple[float, float, float]:
    t0 = time.perf_counter()
    wav, sr, _ = model.infer(  # type: ignore[attr-defined]
        ref_file=str(ref_path),
        ref_text=ref_text,
        gen_text=text,
        remove_silence=True,
    )
    ttfa = time.perf_counter() - t0
    dur = len(np.array(wav)) / int(sr)
    rtf = ttfa / dur if dur > 0 else float("inf")
    return ttfa, rtf, get_vram_mb()


def _once_cosyvoice(
    model: object,
    ref_path: Path,
    ref_text: str,
    text: str,
    prompt_audio: object,
) -> tuple[float, float, float]:
    t0 = time.perf_counter()
    first = True
    ttfa = 0.0
    total_dur = 0.0
    for chunk in model.inference_zero_shot(text, ref_text, prompt_audio):  # type: ignore[attr-defined]
        if first:
            ttfa = time.perf_counter() - t0
            first = False
        audio = chunk["tts_speech"].numpy().flatten()
        total_dur += len(audio) / model.sample_rate  # type: ignore[attr-defined]
    if first:  # 출력 없음
        ttfa = time.perf_counter() - t0
    elapsed = time.perf_counter() - t0
    rtf = elapsed / total_dur if total_dur > 0 else float("inf")
    return ttfa, rtf, get_vram_mb()


# ── 모델 로드 ──────────────────────────────────────────────────────────────


def _load_f5tts(device: str) -> object:
    from f5_tts.api import F5TTS

    logger.info("F5-TTS 로드 중...")
    return F5TTS(device=device)


def _load_cosyvoice() -> tuple[object, object]:
    import torchaudio
    from cosyvoice.cli.cosyvoice import CosyVoice2  # type: ignore[import]

    logger.info("CosyVoice2 로드 중...")
    model = CosyVoice2("CosyVoice2-0.5B")
    return model, torchaudio


# ── 벤치 실행 ──────────────────────────────────────────────────────────────


def bench(
    model_name: str,
    ref_path: Path,
    ref_text: str,
    device: str,
    iterations: int,
) -> dict:
    results: dict[str, list[dict]] = {k: [] for k in _TEST_SENTENCES}

    if model_name == "f5tts":
        model = _load_f5tts(device)

        for label, text in _TEST_SENTENCES.items():
            logger.info("[f5tts / %s] 웜업 + %d회 측정...", label, iterations)
            for i in range(iterations + 1):
                ttfa, rtf, vram = _once_f5tts(model, ref_path, ref_text, text)
                if i == 0:
                    logger.info("  웜업: TTFA=%.3fs RTF=%.3f VRAM=%.0fMB", ttfa, rtf, vram)
                    continue
                results[label].append({"ttfa": ttfa, "rtf": rtf, "vram": vram})
                logger.info("  #%02d TTFA=%.3fs RTF=%.3f VRAM=%.0fMB", i, ttfa, rtf, vram)

    elif model_name == "cosyvoice":
        model, torchaudio = _load_cosyvoice()
        prompt_audio, orig_sr = torchaudio.load(str(ref_path))
        if orig_sr != 16_000:
            prompt_audio = torchaudio.functional.resample(prompt_audio, orig_sr, 16_000)
        if prompt_audio.shape[0] > 1:
            prompt_audio = prompt_audio.mean(dim=0, keepdim=True)

        for label, text in _TEST_SENTENCES.items():
            logger.info("[cosyvoice / %s] 웜업 + %d회 측정...", label, iterations)
            for i in range(iterations + 1):
                ttfa, rtf, vram = _once_cosyvoice(model, ref_path, ref_text, text, prompt_audio)
                if i == 0:
                    logger.info("  웜업: TTFA=%.3fs RTF=%.3f VRAM=%.0fMB", ttfa, rtf, vram)
                    continue
                results[label].append({"ttfa": ttfa, "rtf": rtf, "vram": vram})
                logger.info("  #%02d TTFA=%.3fs RTF=%.3f VRAM=%.0fMB", i, ttfa, rtf, vram)
    else:
        raise ValueError(f"지원하지 않는 모델: {model_name} (f5tts / cosyvoice)")

    # 요약
    summary = {}
    for label, rows in results.items():
        ttfas = np.array([r["ttfa"] for r in rows])
        rtfs  = np.array([r["rtf"]  for r in rows])
        vrams = np.array([r["vram"] for r in rows])
        p95_ttfa = float(np.percentile(ttfas, 95))
        passed = p95_ttfa <= 1.5 and float(np.max(vrams)) <= VRAM_LIMIT_MB
        summary[label] = {
            "ttfa_mean_s":  round(float(np.mean(ttfas)), 3),
            "ttfa_p95_s":   round(p95_ttfa, 3),
            "rtf_mean":     round(float(np.mean(rtfs)), 3),
            "vram_peak_mb": round(float(np.max(vrams)), 1),
            "pass":         passed,
        }
        logger.info(
            "[%s / %s] avg=%.3fs p95=%.3fs RTF=%.3f VRAM=%.0fMB → %s",
            model_name, label,
            summary[label]["ttfa_mean_s"],
            summary[label]["ttfa_p95_s"],
            summary[label]["rtf_mean"],
            summary[label]["vram_peak_mb"],
            "PASS ✅" if passed else "FAIL ❌",
        )

    all_pass = all(v["pass"] for v in summary.values())
    return {
        "model": model_name,
        "device": device,
        "iterations": iterations,
        "summary": summary,
        "stage2_pass": all_pass,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 2 — TTS 추론 속도 실측")
    parser.add_argument(
        "--model", required=True, choices=("f5tts", "cosyvoice"), help="측정 모델"
    )
    parser.add_argument("--reference", type=Path, required=True, help="레퍼런스 WAV")
    parser.add_argument("--ref-text", required=True, help="레퍼런스 전사")
    parser.add_argument("--device", default="cuda", help="추론 장치 (기본 cuda)")
    parser.add_argument(
        "--iterations", type=int, default=ITERATIONS, help=f"반복 횟수 (기본 {ITERATIONS})"
    )
    parser.add_argument("--output", type=Path, help="결과 JSON 저장 경로")
    args = parser.parse_args()

    if not args.reference.exists():
        parser.error(f"레퍼런스 없음: {args.reference}")

    result = bench(args.model, args.reference, args.ref_text, args.device, args.iterations)

    label = "PASS ✅ (웜 TTFA p95 ≤1.5s, VRAM ≤8GB)" if result["stage2_pass"] else "FAIL ❌"
    logger.info("=== Stage 2 결과: %s ===", label)

    js = json.dumps(result, ensure_ascii=False, indent=2)
    print(js)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(js, encoding="utf-8")
        logger.info("결과 저장: %s", args.output)

    if result["stage2_pass"]:
        logger.info("다음 단계: Stage 3 — Conductor 배선 + 바지인 체감 검증")
    else:
        logger.info("판정: 더 빠른 모델 우선 검토 또는 클라우드 폴백 (TTS_전환.md 참조)")


if __name__ == "__main__":
    main()
