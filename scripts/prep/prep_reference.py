"""Stage 1.5 — 레퍼런스 오디오 전처리.

캐릭터 대사 파일 → 보이스 클로닝용 깨끗한 3~10초 레퍼런스 WAV.
  1. BGM·효과음 분리 (demucs — 선택)
  2. 무음 트림 (librosa)
  3. 가장 에너지가 높은 MAX_SEC 구간 선택
  4. 샘플레이트 정규화 (기본 24000 Hz)

사용:
  # BGM 분리 포함
  python scripts/prep/prep_reference.py input.wav -o ref_clean.wav

  # BGM 분리 없이 (이미 보컬만 있는 파일)
  python scripts/prep/prep_reference.py input.wav -o ref_clean.wav --no-demucs

필요 패키지:
  pip install soundfile librosa numpy
  pip install demucs          # BGM 분리 원할 경우 (선택사항)
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import tempfile
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

TARGET_SR = 24_000
MIN_SEC = 3.0
MAX_SEC = 10.0
TOP_DB = 20.0  # 이 이하 dB를 무음으로 처리


def load_audio(path: Path, target_sr: int) -> tuple[np.ndarray, int]:
    import librosa

    audio, sr = librosa.load(str(path), sr=target_sr, mono=True)
    return audio.astype(np.float32), sr


def separate_vocals(input_path: Path, work_dir: Path) -> Path:
    """demucs로 보컬만 추출. 미설치 시 원본 경로 반환."""
    try:
        import demucs  # noqa: F401
    except ImportError:
        logger.warning(
            "demucs 미설치 — BGM 분리 건너뜀. pip install demucs 로 설치 가능."
        )
        return input_path

    logger.info("demucs 보컬 분리 중 (최초 실행 시 모델 다운로드 있을 수 있습니다)...")
    subprocess.run(
        [
            "python",
            "-m",
            "demucs",
            "--two-stems=vocals",
            "-o",
            str(work_dir),
            str(input_path),
        ],
        check=True,
    )
    # demucs 출력: {work_dir}/htdemucs/{입력파일명}/vocals.wav
    candidates = sorted(work_dir.rglob("vocals.wav"))
    if not candidates:
        logger.warning("demucs 출력 vocals.wav를 찾지 못함 — 원본 사용.")
        return input_path
    vocal_path = candidates[-1]
    logger.info("보컬 분리 완료: %s", vocal_path)
    return vocal_path


def trim_silence(audio: np.ndarray, sr: int) -> np.ndarray:
    import librosa

    trimmed, _ = librosa.effects.trim(audio, top_db=TOP_DB)
    return trimmed


def select_segment(audio: np.ndarray, sr: int) -> np.ndarray:
    """전체 오디오에서 RMS 에너지가 가장 높은 MAX_SEC 구간 선택.

    이미 MAX_SEC 이하면 그대로 반환.
    """
    total_sec = len(audio) / sr
    if total_sec <= MAX_SEC:
        return audio

    win_len = int(MAX_SEC * sr)
    hop = int(0.1 * sr)

    best_start = 0
    best_energy = -1.0
    for start in range(0, len(audio) - win_len, hop):
        seg = audio[start : start + win_len]
        energy = float(np.sqrt(np.mean(seg**2)))
        if energy > best_energy:
            best_energy = energy
            best_start = start

    return audio[best_start : best_start + win_len]


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 1.5 — 레퍼런스 오디오 전처리")
    parser.add_argument("input", type=Path, help="원본 오디오 파일 (wav/mp3/flac 등)")
    parser.add_argument("-o", "--output", type=Path, required=True, help="출력 WAV 경로")
    parser.add_argument(
        "--no-demucs", action="store_true", help="BGM·효과음 분리(demucs) 건너뜀"
    )
    parser.add_argument(
        "--sr",
        type=int,
        default=TARGET_SR,
        help=f"출력 샘플레이트 Hz (기본 {TARGET_SR})",
    )
    parser.add_argument(
        "--max-sec",
        type=float,
        default=MAX_SEC,
        help=f"최대 구간 길이 초 (기본 {MAX_SEC})",
    )
    args = parser.parse_args()

    if not args.input.exists():
        parser.error(f"입력 파일 없음: {args.input}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        # 1. BGM 분리
        source = args.input if args.no_demucs else separate_vocals(args.input, tmp_dir)

        # 2. 로드
        logger.info("로드: %s", source)
        audio, sr = load_audio(source, args.sr)
        logger.info("  원본: %.2f초 (%d샘플, %dHz)", len(audio) / sr, len(audio), sr)

        # 3. 무음 트림
        trimmed = trim_silence(audio, sr)
        logger.info("  트림 후: %.2f초", len(trimmed) / sr)

        # 4. 구간 선택
        segment = select_segment(trimmed, sr)
        duration = len(segment) / sr
        logger.info("  선택 구간: %.2f초", duration)

        if duration < MIN_SEC:
            logger.warning(
                "구간이 %.2f초로 최소 %.1f초 미만입니다. 레퍼런스 품질이 낮을 수 있습니다.",
                duration,
                MIN_SEC,
            )

        # 5. 저장
        import soundfile as sf

        args.output.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(args.output), segment, sr, subtype="PCM_16")
        logger.info("저장 완료: %s (%.2f초, %dHz)", args.output, duration, sr)
        logger.info("다음 단계: python scripts/try/try_clone.py --reference %s --ref-text '여기에 레퍼런스 전사 입력'", args.output)


if __name__ == "__main__":
    main()
