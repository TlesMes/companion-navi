"""Stage 1.6 — GPT-SoVITS fine-tune 데이터셋 빌더.

voice_ref/ 아래 캐릭터 폴더(각각 transcription.csv + *.ogg)를 모아
GPT-SoVITS 학습용 산출물을 만든다.
  1. ogg → wav 변환 (mono, 원본 SR 유지 — GPT-SoVITS 전처리가 32k로 리샘플)
  2. .list 주석 파일 작성 (`wav경로|화자|언어|전사`)
  3. (선택) Colab 업로드용 zip 패키징

전사 CSV 포맷: `filename,transcription` 헤더 + 행. filename은 폴더 내 ogg 파일명.
전사가 없는 기합/무대사 클립은 CSV에 없으므로 자연히 제외된다.

사용:
  # 기본 — voice_ref 전체를 dataset/arisu 로
  python scripts/prep/build_sovits_dataset.py

  # 폴더·화자·언어·출력 지정
  python scripts/prep/build_sovits_dataset.py \\
      --src voice_ref/Arisu_Maid voice_ref/Arisu \\
      --speaker Arisu --lang ja --out dataset/arisu --zip

.list 경로 접두사(--wav-prefix)는 학습 환경 기준으로 맞춘다.
Colab이면 업로드 후 wav 폴더 경로(예: /content/dataset/arisu/wavs)로 지정·재생성.

필요 패키지: soundfile, numpy  (librosa·ffmpeg 불필요)
"""

from __future__ import annotations

import argparse
import csv
import logging
import zipfile
from pathlib import Path

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def _safe_stem(ogg_name: str) -> str:
    """파일명에서 괄호·공백 제거 (학습 스크립트 호환). 확장자는 .wav."""
    stem = Path(ogg_name).stem
    for ch in "()[] ":
        stem = stem.replace(ch, "_" if ch == " " else "")
    return stem


def read_csv(csv_path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with csv_path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            fn = (row.get("filename") or "").strip()
            tx = (row.get("transcription") or "").strip()
            if fn and tx:
                rows.append((fn, tx))
    return rows


def convert_ogg(src: Path, dst: Path) -> float:
    """ogg → wav(mono, PCM16, 원본 SR). 반환: 길이(초)."""
    audio, sr = sf.read(str(src), dtype="float32", always_2d=True)
    mono = audio.mean(axis=1)
    dst.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(dst), mono, sr, subtype="PCM_16")
    return len(mono) / sr


def main() -> None:
    parser = argparse.ArgumentParser(description="GPT-SoVITS fine-tune 데이터셋 빌더")
    parser.add_argument(
        "--src",
        type=Path,
        nargs="+",
        default=[Path("voice_ref/Arisu_Maid"), Path("voice_ref/Arisu")],
        help="캐릭터 폴더(들). 각 폴더에 transcription.csv + *.ogg",
    )
    parser.add_argument("--speaker", default="Arisu", help="화자명 (.list 2번째 칸)")
    parser.add_argument("--lang", default="ja", help="언어 코드 (.list 3번째 칸, 기본 ja)")
    parser.add_argument(
        "--out", type=Path, default=Path("dataset/arisu"), help="출력 디렉터리"
    )
    parser.add_argument(
        "--wav-prefix",
        default=None,
        metavar="PATH",
        help=".list의 wav 경로 접두사 (기본: 출력 wavs 폴더의 절대경로). "
        "Colab 학습 시 Colab 경로로 지정.",
    )
    parser.add_argument("--zip", action="store_true", help="결과를 zip으로도 패키징")
    args = parser.parse_args()

    wav_dir = args.out / "wavs"
    wav_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.wav_prefix if args.wav_prefix is not None else str(wav_dir.resolve())
    prefix = prefix.rstrip("/")

    list_lines: list[str] = []
    total_sec = 0.0
    n_ok = 0
    n_missing = 0

    for folder in args.src:
        csv_path = folder / "transcription.csv"
        if not csv_path.exists():
            logger.warning("transcription.csv 없음 — 건너뜀: %s", folder)
            continue
        rows = read_csv(csv_path)
        logger.info("[%s] 전사 %d행", folder.name, len(rows))
        for fn, tx in rows:
            src = folder / fn
            if not src.exists():
                logger.warning("  음원 없음: %s", src)
                n_missing += 1
                continue
            wav_name = _safe_stem(fn) + ".wav"
            dur = convert_ogg(src, wav_dir / wav_name)
            total_sec += dur
            n_ok += 1
            list_lines.append(f"{prefix}/{wav_name}|{args.speaker}|{args.lang}|{tx}")

    list_path = args.out / f"{args.speaker.lower()}.list"
    list_path.write_text("\n".join(list_lines) + "\n", encoding="utf-8")

    logger.info("=== 완료 ===")
    logger.info("  변환 %d개 (음원 누락 %d), 총 %.1f분", n_ok, n_missing, total_sec / 60)
    logger.info("  wav: %s", wav_dir)
    logger.info("  list: %s  (경로 접두사 %s)", list_path, prefix)

    if args.zip:
        zip_path = args.out.with_suffix(".zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(list_path, list_path.name)
            for w in sorted(wav_dir.glob("*.wav")):
                zf.write(w, f"wavs/{w.name}")
        logger.info("  zip: %s (%.1f MB)", zip_path, zip_path.stat().st_size / 1e6)

    logger.info("")
    logger.info("다음: Colab에 업로드 → GPT-SoVITS 1-format(text/hubert/semantic) → 학습")
    logger.info("  Colab 경로에 맞춰 .list 재생성: --wav-prefix /content/.../wavs")


if __name__ == "__main__":
    main()
