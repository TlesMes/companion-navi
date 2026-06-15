"""Stage 1 — Zero-shot 보이스 클로닝 음색 품질 비교.

레퍼런스 오디오(3~10초) + 텍스트 → 모델별 합성 샘플 WAV 일괄 생성.
생성된 파일을 들으며 블라인드 비교 후 D3(TTS 음색) 결정.

사용:
  # 단일 모델
  python scripts/try/try_clone.py --model f5tts \\
      --reference ref.wav --ref-text "레퍼런스 오디오 내용" \\
      --text "합성할 텍스트"

  # 전체 후보 비교 (설치된 모델만 자동 시도)
  python scripts/try/try_clone.py --all \\
      --reference ref.wav --ref-text "..." \\
      --texts sentences.txt

후보 모델:
  f5tts     — pip install f5-tts (플로우매칭, 빠른 추론)
  cosyvoice — pip install git+https://github.com/FunAudioLLM/CosyVoice.git (다국어 제로샷)
  gptsovits — GPT-SoVITS repo 클론 필요, PYTHONPATH 설정 또는 --sovits-repo 지정

설치 (WSL2 + ROCm):
  pip install f5-tts soundfile numpy
  # CosyVoice2: repo clone 후 pip install -r requirements.txt
  # GPT-SoVITS: git clone https://github.com/RVC-Boss/GPT-SoVITS /opt/gptsovits

평가 항목 (블라인드 1~5점):
  음색 닮음도 / 한국어 발음·억양 자연스러움 / 잡음·아티팩트 / 감정선
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

_ALL_MODELS = ("f5tts", "cosyvoice", "gptsovits")

# 기본 비교용 텍스트 (--text 미지정 시)
_DEFAULT_TEXTS = [
    "응, 그래. 오늘 기분은 어때?",
    "요즘 날씨가 참 이상하지 않아? 갑자기 추워졌어.",
    "나 오늘 네 생각 많이 했어. 그냥 얘기하고 싶었거든.",
    "있잖아, 나 요즘 네가 없으면 심심해. 진짜로.",
    "그거 알아? 네가 웃을 때가 제일 좋더라.",
]


# ── 모델별 추론 ────────────────────────────────────────────────────────────


def _infer_f5tts(
    ref_path: Path, ref_text: str, gen_text: str, device: str
) -> tuple[np.ndarray, int]:
    from f5_tts.api import F5TTS

    model = F5TTS(device=device)
    wav, sr, _ = model.infer(
        ref_file=str(ref_path),
        ref_text=ref_text,
        gen_text=gen_text,
        remove_silence=True,
    )
    return np.array(wav, dtype=np.float32), int(sr)


def _infer_cosyvoice(
    ref_path: Path, ref_text: str, gen_text: str, device: str
) -> tuple[np.ndarray, int]:
    import torchaudio
    from cosyvoice.cli.cosyvoice import CosyVoice2  # type: ignore[import]

    model = CosyVoice2("CosyVoice2-0.5B")
    prompt_audio, orig_sr = torchaudio.load(str(ref_path))
    if orig_sr != 16_000:
        prompt_audio = torchaudio.functional.resample(prompt_audio, orig_sr, 16_000)
    if prompt_audio.shape[0] > 1:
        prompt_audio = prompt_audio.mean(dim=0, keepdim=True)

    chunks = []
    for chunk in model.inference_zero_shot(gen_text, ref_text, prompt_audio):
        chunks.append(chunk["tts_speech"].numpy().flatten())

    wav = np.concatenate(chunks).astype(np.float32) if chunks else np.zeros(1, np.float32)
    return wav, int(model.sample_rate)


def _infer_gptsovits(
    ref_path: Path,
    ref_text: str,
    gen_text: str,
    device: str,
    repo_path: str | None = None,
    gpt_ckpt: str | None = None,
) -> tuple[np.ndarray, int]:
    """GPT-SoVITS repo가 PYTHONPATH에 있거나 repo_path로 지정돼야 함."""
    import sys

    if repo_path:
        sys.path.insert(0, repo_path)

    try:
        from GPT_SoVITS.inference_webui import (  # type: ignore[import]
            change_gpt_weights,
            get_tts_wav,
        )
    except ImportError as exc:
        raise ImportError(
            "GPT-SoVITS를 찾을 수 없습니다.\n"
            "  git clone https://github.com/RVC-Boss/GPT-SoVITS /opt/gptsovits\n"
            "  --sovits-repo /opt/gptsovits 또는 PYTHONPATH 설정"
        ) from exc

    if gpt_ckpt:
        change_gpt_weights(gpt_ckpt)

    sr = 32_000
    chunks = []
    for chunk in get_tts_wav(
        ref_wav_path=str(ref_path),
        prompt_text=ref_text,
        prompt_language="ko",
        text=gen_text,
        text_language="ko",
    ):
        chunks.append(chunk)

    if not chunks:
        return np.zeros(1, np.float32), sr

    wav_i16 = np.concatenate([np.frombuffer(c, dtype=np.int16) for c in chunks])
    return (wav_i16.astype(np.float32) / 32768.0), sr


_INFER = {
    "f5tts": _infer_f5tts,
    "cosyvoice": _infer_cosyvoice,
}


# ── 단일 실행 ──────────────────────────────────────────────────────────────


def run_one(
    model: str,
    ref_path: Path,
    ref_text: str,
    gen_text: str,
    out_dir: Path,
    tag: str,
    device: str,
    sovits_repo: str | None,
    gpt_ckpt: str | None,
) -> Path | None:
    out_path = out_dir / f"{tag}_{model}.wav"
    if out_path.exists():
        logger.info("[%s] 이미 존재 — 스킵: %s", model, out_path.name)
        return out_path

    logger.info("[%s] 합성: %r...", model, gen_text[:40])
    t0 = time.perf_counter()
    try:
        if model == "gptsovits":
            wav, sr = _infer_gptsovits(
                ref_path, ref_text, gen_text, device, sovits_repo, gpt_ckpt
            )
        elif model in _INFER:
            wav, sr = _INFER[model](ref_path, ref_text, gen_text, device)
        else:
            logger.error("알 수 없는 모델: %s", model)
            return None
    except Exception:
        logger.exception("[%s] 합성 실패 — 다음 모델로", model)
        return None

    elapsed = time.perf_counter() - t0
    out_dir.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_path), wav, sr, subtype="PCM_16")
    dur = len(wav) / sr
    logger.info("[%s] 완료 %.2f초 (합성 %.1fs, 오디오 %.1fs) → %s", model, elapsed, elapsed, dur, out_path.name)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 1 — Zero-shot TTS 음색 비교")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--model", choices=_ALL_MODELS, help="테스트할 단일 모델")
    g.add_argument(
        "--all",
        action="store_true",
        help="설치된 모든 후보 모델 시도 (실패한 것은 건너뜀)",
    )
    parser.add_argument("--reference", type=Path, required=True, help="레퍼런스 WAV (3~10초)")
    parser.add_argument(
        "--ref-text",
        required=True,
        metavar="TEXT",
        help="레퍼런스 오디오의 전사 텍스트",
    )
    parser.add_argument(
        "--text",
        action="append",
        metavar="TEXT",
        dest="texts",
        help="합성할 텍스트 (여러 번 지정 가능)",
    )
    parser.add_argument(
        "--texts-file",
        type=Path,
        metavar="FILE",
        help="합성 텍스트 목록 파일 (줄 단위)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("out/clone"),
        help="출력 디렉터리 (기본 out/clone/)",
    )
    parser.add_argument("--device", default="cuda", help="추론 장치 (cuda / cpu)")
    parser.add_argument("--sovits-repo", metavar="PATH", help="GPT-SoVITS repo 경로")
    parser.add_argument("--gpt-ckpt", metavar="PATH", help="GPT-SoVITS GPT 모델 .ckpt 경로")
    args = parser.parse_args()

    if not args.reference.exists():
        parser.error(f"레퍼런스 파일 없음: {args.reference}")

    texts: list[str] = list(args.texts or [])
    if args.texts_file:
        lines = args.texts_file.read_text(encoding="utf-8").splitlines()
        texts += [ln.strip() for ln in lines if ln.strip()]
    if not texts:
        texts = _DEFAULT_TEXTS
        logger.info("--text 미지정 — 기본 텍스트 %d개로 합성합니다.", len(texts))

    models = list(_ALL_MODELS) if args.all else [args.model]

    outputs: list[Path] = []
    for i, text in enumerate(texts):
        tag = f"t{i:02d}"
        for model in models:
            result = run_one(
                model,
                args.reference,
                args.ref_text,
                text,
                args.output,
                tag,
                args.device,
                args.sovits_repo,
                args.gpt_ckpt,
            )
            if result:
                outputs.append(result)

    logger.info("=== 생성 완료 (%d개) ===", len(outputs))
    for p in outputs:
        logger.info("  %s", p)
    logger.info("")
    logger.info("다음 단계:")
    logger.info("  생성된 WAV를 블라인드로 들으며 1~5점 채점:")
    logger.info("  음색 닮음도 / 한국어 발음 자연스러움 / 잡음·아티팩트 / 감정선")
    logger.info("  채점 결과를 docs/04_progress.md 에 D3 근거로 기록 후 Stage 2로.")


if __name__ == "__main__":
    main()
