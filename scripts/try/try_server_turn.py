"""턴 게이트웨이 스모크 — 서버에 기기 토큰을 받아 붙고 턴을 보내 스트림을 눈으로 본다.

서버(`server/`)를 띄워 둔 상태에서 돌린다:

    cd server && ./gradlew bootRun            # 다른 터미널. 실제 모델은 NAVI_BRAIN_VENDOR=anthropic
    python scripts/try/try_server_turn.py "나 방금 퇴근했어" "저녁 뭐 먹지?"

발화를 여러 개 주면 한 연결에서 차례로 보낸다 — 둘째 턴부터 서버가 앞 턴을 기억하는지 보인다.
`--overlong`은 한도를 넘는 발화를 하나 더 보내 거절되는 것을 보인다.

프로토콜은 docs/design/protocol.md. 이 스크립트는 데몬의 연결 모드가 생기기 전의 **참고
클라이언트**이기도 하다 — 마이크·TTS 없이 텍스트만 오간다.

`websockets`가 필요하다(uvicorn을 따라 이미 깔려 있다).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.request
import uuid

import websockets


def register(base_url: str) -> dict:
    """새 기기를 등록한다 — 익명 사용자와 토큰이 생긴다. 토큰은 이 응답에서만 볼 수 있다."""
    request = urllib.request.Request(f"{base_url}/v1/devices", method="POST", data=b"")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


async def run_turn(ws, persona: str, text: str, *, played_ratio: float) -> bool:
    """턴 하나. 끝까지 갔으면 True, 거절·실패면 False."""
    turn_id = str(uuid.uuid4())
    started = time.perf_counter()
    await ws.send(json.dumps(
        {"type": "turn.start", "turn_id": turn_id, "kind": "reactive",
         "persona": persona, "text": text}
    ))
    print(f"\n> {text if len(text) <= 60 else text[:20] + f'… ({len(text)}자)'}")

    body = ""
    first_delta_ms = None
    while True:
        msg = json.loads(await ws.recv())
        kind = msg["type"]
        if kind == "turn.accepted":
            continue
        if kind == "turn.mood":
            print(f"  [무드: {msg['mood']}]")
        elif kind == "turn.delta":
            if first_delta_ms is None:
                first_delta_ms = (time.perf_counter() - started) * 1000
                print("  ", end="")
            body += msg["text"]
            print(msg["text"], end="", flush=True)
        elif kind == "turn.end":
            print()
            assert len(body) == msg["chars"], "받은 본문 길이가 서버가 말한 길이와 다르다"
            print(
                f"  — 조각 {msg['last_seq'] + 1}개, {msg['chars']}자, "
                f"첫 조각 {first_delta_ms:.0f}ms(클라이언트) / 두뇌 첫 토큰 "
                f"{msg['server_ms']['ttft']}ms / 전체 {msg['server_ms']['total']}ms, "
                f"토큰 입력 {msg['usage']['input_tokens']} · 출력 {msg['usage']['output_tokens']} "
                f"· 캐시 읽기 {msg['usage']['cache_read_tokens']}"
            )
            break
        elif kind == "turn.error":
            retry = f", {msg['retry_after_s']}초 뒤 다시" if "retry_after_s" in msg else ""
            print(f"  ✗ 거절: {msg['code']} — {msg['message']}{retry}")
            return False
        elif kind == "turn.cancelled":
            print("\n  (취소됨)")
            return False

    # 재생 보고 — 실제 데몬은 TTS가 어디까지 소리 냈는지를 보낸다. 여기선 비율로 흉내 낸다.
    played = round(len(body) * played_ratio)
    await ws.send(json.dumps(
        {"type": "turn.played", "turn_id": turn_id, "played_chars": played,
         "interrupted": played < len(body)}
    ))
    confirmed = json.loads(await ws.recv())
    assert confirmed["type"] == "turn.confirmed" and confirmed["ok"], confirmed
    print(f"  — 재생 보고 {played}/{len(body)}자 → 확정")
    await ws.send(json.dumps(
        {"type": "turn.metrics", "turn_id": turn_id,
         "metrics": {"first_delta_ms": round(first_delta_ms or 0)}}
    ))
    return True


async def main(args: argparse.Namespace) -> int:
    http_base = args.url.rstrip("/")
    ws_url = http_base.replace("http", "ws", 1) + "/v1/turn-stream"

    device = register(http_base)
    print(f"기기 등록 — user_id={device['user_id']} device_id={device['device_id']} (토큰은 출력하지 않음)")

    headers = {"Authorization": f"Bearer {device['device_token']}"}
    async with websockets.connect(ws_url, additional_headers=headers) as ws:
        ready = json.loads(await ws.recv())
        print(f"연결 — 프로토콜 v{ready['protocol']}, 페르소나 {ready['personas']}, 한도 {ready['limits']}")

        ok = True
        for text in args.text:
            ok = await run_turn(ws, args.persona, text, played_ratio=args.played) and ok

        if args.overlong:
            limit = ready["limits"]["max_text_chars"]
            print(f"\n(한도 {limit}자를 넘는 발화를 보낸다)")
            rejected = not await run_turn(ws, args.persona, "가" * (limit + 1), played_ratio=1.0)
            ok = ok and rejected
    return 0 if ok else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔 기본(cp949)에서 깨지지 않게
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("text", nargs="*", default=["나 방금 퇴근했어."], help="보낼 발화(여러 개 가능)")
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--persona", default="navi")
    parser.add_argument("--played", type=float, default=1.0, help="재생 보고 비율(0~1). 끼어들기 흉내")
    parser.add_argument("--overlong", action="store_true", help="한도 초과 발화를 보내 거절을 확인")
    sys.exit(asyncio.run(main(parser.parse_args())))
