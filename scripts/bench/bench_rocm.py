"""Stage 0 — ROCm 환경 진단 + 스트레스 테스트.

합격 기준:
  1. torch.cuda.is_available() == True (ROCm은 CUDA 인터페이스로 노출됨)
  2. 5분 연속 matmul — 크래시·행(hang) 없음
  3. VRAM 여유 확인 (LLM API 사용 전제, 6GB 이상 마진 목표)

WSL2 + Ubuntu + ROCm 설치 후 실행:
  HSA_OVERRIDE_GFX_VERSION=10.3.0 python scripts/bench/bench_rocm.py

gfx1032(RX 6600 XT)는 ROCm 공식 미지원 → 10.3.0(gfx1030)으로 위장 필요.

ROCm + PyTorch 설치 (Ubuntu 22.04 기준):
  # 1. ROCm 설치
  wget https://repo.radeon.com/amdgpu-install/6.1.3/ubuntu/jammy/amdgpu-install_6.1.60103-1_all.deb
  sudo dpkg -i amdgpu-install_6.1.60103-1_all.deb
  sudo amdgpu-install --usecase=rocm
  sudo usermod -aG render,video $USER  # 재로그인 필요

  # 2. ROCm PyTorch
  pip install torch torchvision --index-url https://download.pytorch.org/whl/rocm6.1

  # 3. 환경변수 (.bashrc에 추가)
  export HSA_OVERRIDE_GFX_VERSION=10.3.0
  export ROCR_VISIBLE_DEVICES=0
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def check_torch_rocm() -> dict:
    try:
        import torch
    except ImportError:
        return {"available": False, "error": "torch 미설치 — pip install torch --index-url https://download.pytorch.org/whl/rocm6.1"}

    info: dict = {"torch_version": torch.__version__}
    info["cuda_available"] = torch.cuda.is_available()

    if not info["cuda_available"]:
        info["hint"] = "HSA_OVERRIDE_GFX_VERSION=10.3.0 설정 여부 확인"
        return info

    info["device_count"] = torch.cuda.device_count()
    info["device_name"] = torch.cuda.get_device_name(0)
    props = torch.cuda.get_device_properties(0)
    info["vram_total_gb"] = round(props.total_memory / 1e9, 2)
    info["vram_free_gb"] = round(
        (props.total_memory - torch.cuda.memory_allocated(0)) / 1e9, 2
    )
    # ROCm 버전 (있으면)
    try:
        info["hip_version"] = torch.version.hip  # type: ignore[attr-defined]
    except AttributeError:
        pass
    return info


def stress_test(duration_sec: int = 300, size: int = 4096) -> dict:
    """size×size float32 matmul을 duration_sec 동안 반복. 크래시 없으면 합격."""
    import torch

    device = torch.device("cuda")
    a = torch.randn(size, size, device=device, dtype=torch.float32)
    b = torch.randn(size, size, device=device, dtype=torch.float32)

    start = time.perf_counter()
    iterations = 0
    errors = 0
    peak_vram_gb = 0.0

    logger.info("스트레스 테스트 시작 (%d초, float32 matmul %dx%d)...", duration_sec, size, size)
    while time.perf_counter() - start < duration_sec:
        try:
            _ = torch.mm(a, b)
            torch.cuda.synchronize()
            iterations += 1
            vram = torch.cuda.memory_allocated(0) / 1e9
            if vram > peak_vram_gb:
                peak_vram_gb = vram
        except Exception as exc:
            errors += 1
            logger.error("반복 %d 오류: %s", iterations, exc)
            if errors >= 5:
                logger.error("오류 5회 초과 — 중단.")
                break

        if iterations % 200 == 0:
            elapsed = time.perf_counter() - start
            logger.info("  %d회 / %.0f초 경과 / VRAM %.3fGB", iterations, elapsed, peak_vram_gb)

    elapsed = time.perf_counter() - start
    return {
        "duration_sec": round(elapsed, 1),
        "iterations": iterations,
        "errors": errors,
        "peak_vram_gb": round(peak_vram_gb, 3),
        "passed": errors == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 0 — ROCm 환경 진단")
    parser.add_argument(
        "--stress-sec",
        type=int,
        default=300,
        metavar="SEC",
        help="스트레스 테스트 시간(초). 0이면 스킵 (기본 300=5분)",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=4096,
        help="matmul 행렬 크기 (기본 4096)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="결과 JSON 저장 경로",
    )
    args = parser.parse_args()

    result: dict = {}

    # 1. 환경 체크
    logger.info("=== ROCm 환경 체크 ===")
    env = check_torch_rocm()
    result["env"] = env
    for k, v in env.items():
        logger.info("  %s: %s", k, v)

    if not env.get("cuda_available"):
        logger.error("torch.cuda.is_available() == False — ROCm 미인식.")
        logger.error("  export HSA_OVERRIDE_GFX_VERSION=10.3.0 확인")
        logger.error("  ROCm PyTorch 설치 확인 (스크립트 상단 주석 참조)")
        result["stage0_pass"] = False
        _dump(result, args.output)
        return

    vram_total = env.get("vram_total_gb", 0)
    if vram_total < 6.0:
        logger.warning("VRAM %.2fGB — 6GB 미만. 대형 보이스 클로닝 모델 로드 불가 가능.", vram_total)

    # 2. 스트레스 테스트
    if args.stress_sec > 0:
        logger.info("=== 스트레스 테스트 (%d초) ===", args.stress_sec)
        stress = stress_test(duration_sec=args.stress_sec, size=args.size)
        result["stress"] = stress
        if stress["passed"]:
            logger.info(
                "PASS — %d회 반복 / 에러 0 / VRAM 최고 %.3fGB",
                stress["iterations"],
                stress["peak_vram_gb"],
            )
        else:
            logger.error(
                "FAIL — %d회 중 %d 에러",
                stress["iterations"],
                stress["errors"],
            )
    else:
        logger.info("스트레스 테스트 스킵 (--stress-sec 0)")
        result["stress"] = {"skipped": True}

    result["stage0_pass"] = env.get("cuda_available", False) and result["stress"].get(
        "passed", True
    )
    logger.info(
        "=== Stage 0: %s ===", "PASS ✅" if result["stage0_pass"] else "FAIL ❌"
    )
    _dump(result, args.output)


def _dump(result: dict, output: Path | None) -> None:
    js = json.dumps(result, ensure_ascii=False, indent=2)
    print(js)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(js, encoding="utf-8")
        logger.info("결과 저장: %s", output)


if __name__ == "__main__":
    main()
