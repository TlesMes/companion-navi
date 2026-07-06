"""Phase 2 속도 프로파일 — 전 구간 구간별 지연 실측 (발화 파일→STT→Brain 첫 토큰→첫 오디오).

동일 입력 wav로 (웜업 1회 + N회) 턴을 돌려 구간별 평균을 낸다. STT·TTS 엔진은 시작 시
한 번만 로드한다 — 데몬 상주 전제라 웜 상태가 실제 체감 지연이다(~1.5초 목표도 웜 기준).

마이크 경로의 ①발화 종료→엔드포인터 확정은 설계 상수(endpoint_silence_ms=800ms)라
여기서 측정하지 않는다 — 결과 해석 시 E2E에 +800ms로 더한다.

사용 (.venv-voice — PYTHONUTF8=1 필수: GPT-SoVITS가 import 시 중국어를 print해
콘솔/리다이렉트가 cp949면 UnicodeEncodeError로 죽는다):
  PYTHONUTF8=1 python scripts/bench/profile_turn.py --input scripts/in/probe_ko.wav \
      --mouth gptsovits --persona aris --stt-model small --repeats 3

프로브 입력(scripts/in/은 gitignore)이 없으면 Supertonic으로 재생성:
  python -c "from supertonic import TTS; import soundfile as sf; \
    t=TTS(model='supertonic-3',auto_download=True); \
    w,_=t.synthesize(text='나비야, 오늘 하루 어땠어?', \
    voice_style=t.get_voice_style(voice_name='F1'),total_steps=8,speed=1.0,lang='ko'); \
    sf.write('scripts/in/probe_ko.wav', w.reshape(-1), t.sample_rate)"
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import statistics
import sys
import tempfile
import time
import uuid
from pathlib import Path

from navi.brain import create_brain
from navi.conductor import Conductor
from navi.config import load_config
from navi.memory import MemoryStore
from navi.mouth import create_mouth
from navi.persona import CharacterCard
from navi.pipeline import TurnPipeline
from navi.stt.fasterwhisper import FasterWhisperStt

log = logging.getLogger("navi.bench.profile")

# gptsovits 어댑터의 계측 로그(DEBUG/INFO)에서 +ms 값을 뽑는다
_MARKS = {
    "sentence_ms": re.compile(r"첫 문장 확정 \+(\d+)ms"),
    "synth_ms": re.compile(r"첫 문장 합성 완료 \+(\d+)ms"),
    "ttfa_ms": re.compile(r"첫 오디오 재생 \+(\d+)ms"),
}


class _Capture(logging.Handler):
    """navi.mouth 로거의 계측 메시지를 턴 단위로 수집한다."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())

    def extract(self) -> dict[str, float | None]:
        out: dict[str, float | None] = {}
        for key, pat in _MARKS.items():
            hit = next((m for msg in self.messages if (m := pat.search(msg))), None)
            out[key] = float(hit.group(1)) if hit else None
        return out


async def profile(args: argparse.Namespace) -> None:
    config = load_config(mouth_vendor=args.mouth, persona_card=f"personas/{args.persona}.yaml")
    # 본 기억 DB를 오염시키지 않는 임시 DB — 매 턴 기억 0턴(프롬프트 크기 고정 → 반복 간 동일 조건)
    tmp_db = Path(tempfile.mkstemp(suffix=".db")[1])
    store = MemoryStore(tmp_db)
    card = CharacterCard.load(config.persona_card_path)
    brain = create_brain(config)
    conductor = Conductor(card=card, memory=store, config=config)
    user_id = store.ensure_user(display_name="프로파일러")
    session_id = uuid.uuid4().hex

    stt = FasterWhisperStt(model_size=args.stt_model)
    log.info("STT 모델 로딩… (%s)", args.stt_model)
    await asyncio.to_thread(stt.warmup)

    mouth = create_mouth(config.mouth.vendor, **config.mouth.options)
    log.info("TTS 엔진 로딩… (%s)", config.mouth.vendor)
    t_load = time.perf_counter()
    await asyncio.to_thread(mouth.warmup)
    log.info("TTS 엔진 로드 %.1fs (데몬 시작 1회 비용)", time.perf_counter() - t_load)

    pipeline = TurnPipeline(brain=brain, mouth=mouth, conductor=conductor, voice=config.mouth.voice)

    capture = _Capture()
    logging.getLogger("navi.mouth").addHandler(capture)

    rows: list[dict[str, float | None]] = []
    for i in range(args.repeats + 1):
        label = "웜업" if i == 0 else f"#{i}"
        capture.messages.clear()

        stt_t0 = time.perf_counter()
        text, _ = await asyncio.to_thread(stt._transcribe_path, str(args.input), "ko")
        stt_ms = (time.perf_counter() - stt_t0) * 1000
        if not text:
            log.error("[%s] STT 인식 실패 — 중단", label)
            return

        turn_t0 = time.perf_counter()
        first_token_ms: float | None = None

        def _echo(_tok: str) -> None:
            nonlocal first_token_ms
            if first_token_ms is None:
                first_token_ms = (time.perf_counter() - turn_t0) * 1000

        result = await pipeline.run_turn(
            text, user_id=user_id, session_id=session_id, echo=_echo
        )
        total_ms = (time.perf_counter() - turn_t0) * 1000

        row: dict[str, float | None] = {
            "stt_ms": stt_ms,
            "first_token_ms": first_token_ms,
            "total_ms": total_ms,
            **capture.extract(),
        }
        rows.append(row)
        log.info(
            "[%s] STT %.0fms | 첫 토큰 %.0fms | 문장확정 %s | 합성완료 %s | TTFA %s | 응답 %r",
            label, stt_ms, first_token_ms or -1,
            row["sentence_ms"], row["synth_ms"], row["ttfa_ms"],
            (result.full_text[:40] + "…") if result else None,
        )

    store.close()
    _report(rows[1:], args)


def _report(rows: list[dict[str, float | None]], args: argparse.Namespace) -> None:
    def avg(key: str) -> float | None:
        vals = [r[key] for r in rows if r[key] is not None]
        return statistics.mean(vals) if vals else None

    stt = avg("stt_ms") or 0
    tok = avg("first_token_ms") or 0
    sent = avg("sentence_ms") or 0
    synth = avg("synth_ms") or 0
    ttfa = avg("ttfa_ms") or 0
    endpoint = 800.0  # MicListener endpoint_silence_ms 설계 상수
    e2e = endpoint + stt + ttfa

    print()
    print(f"### 구간별 평균 (n={len(rows)}, 웜업 제외 | input={args.input}, "
          f"stt={args.stt_model}, mouth={args.mouth})")
    print()
    print("| 구간 | 지연(ms) | E2E 대비 % |")
    print("| :-- | --: | --: |")
    print(f"| ① 발화 종료 → 엔드포인터 확정 (설계 상수) | {endpoint:.0f} | {endpoint/e2e*100:.0f}% |")
    print(f"| ② STT (faster-whisper {args.stt_model}, 웜) | {stt:.0f} | {stt/e2e*100:.0f}% |")
    print(f"| ③ Brain 첫 토큰 (TTFT) | {tok:.0f} | {tok/e2e*100:.0f}% |")
    print(f"| ③′ 첫 문장 완성 대기 (첫 토큰 이후) | {sent - tok:.0f} | {(sent-tok)/e2e*100:.0f}% |")
    print(f"| ④ TTS 첫 문장 합성 | {synth - sent:.0f} | {(synth-sent)/e2e*100:.0f}% |")
    print(f"| ④′ 재생 시작 오버헤드 | {ttfa - synth:.0f} | {(ttfa-synth)/e2e*100:.0f}% |")
    print(f"| **E2E (발화 종료 → 첫 오디오)** | **{e2e:.0f}** | 100% |")


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 2 속도 프로파일 — 구간별 지연 실측")
    parser.add_argument("--input", type=Path, default=Path("scripts/in/probe_ko.wav"))
    parser.add_argument("--repeats", type=int, default=3, help="측정 횟수 (웜업 1회 별도)")
    parser.add_argument("--stt-model", default="small")
    parser.add_argument("--mouth", default="gptsovits")
    parser.add_argument("--persona", default="aris")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("navi.mouth").setLevel(logging.DEBUG)
    if not args.input.exists():
        parser.error(f"입력 파일 없음: {args.input}")
    # gptsovits _ensure_engine이 CWD를 repo로 옮기므로(os.chdir) 상대경로가 깨진다
    args.input = args.input.resolve()
    try:
        asyncio.run(profile(args))
    except BaseException:
        import traceback

        traceback.print_exc()
        sys.stderr.flush()
        os._exit(1)
    finally:
        # GPT-SoVITS 합성 스레드·PortAudio 잔여가 executor join을 막아 프리즈한다
        # (navi/cli.py main()과 같은 규약) — 결과는 이미 출력됐으므로 즉시 종료.
        sys.stdout.flush()
        os._exit(0)


if __name__ == "__main__":
    main()
