"""연결 모드(navi/remote) — 프로세스 안의 가짜 서버로 프로토콜을 확인한다.

가짜 서버는 design/protocol.md대로 응답한다. 진짜 서버(server/)와의 왕복은 Java 쪽
통합 테스트와 실구동(scripts/try)이 본다. 여기서 보는 건 클라이언트가 프로토콜을 지키는가와,
파이프라인·TTS·기억이 단독 모드와 같은 경로를 타는가다.
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

from navi.backend import TurnBackend
from navi.brain.base import BrainAuthError, BrainUnavailable
from navi.models import BrainResult, TurnKind, Usage, VoiceProfile
from navi.mouth.fake import FakeMouth
from navi.persona import CharacterCard
from navi.pipeline import TurnPipeline
from navi.remote import RemoteBrain, RemoteConductor, ServerError, ServerLink
from navi.remote.brain import RemoteTurnRequest

CARD = CharacterCard.load(Path(__file__).parents[1] / "personas" / "navi.yaml")
TOKEN = "navi_dt_test"
VOICE = VoiceProfile(name="navi", vendor_voice_id="stub")


class FakeServer:
    """턴 스트림 서버 흉내. script(turn.start 메시지) → 보낼 메시지 목록."""

    def __init__(self):
        self.received: list[dict] = []
        self.connections = 0
        self.script = lambda start: [
            {"type": "turn.accepted"},
            {"type": "turn.mood", "mood": "bright"},
            {"type": "turn.delta", "seq": 0, "text": "안녕."},
            {"type": "turn.delta", "seq": 1, "text": " 왔어?"},
            {"type": "turn.end", "last_seq": 1, "chars": 7,
             "usage": {"input_tokens": 50, "output_tokens": 10, "cache_read_tokens": 0}},
        ]
        self.cancelled = asyncio.Event()

    def _auth(self, connection, request):
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return Response(401, "Unauthorized", Headers(), b"")
        return None

    async def _handler(self, ws):
        self.connections += 1
        await ws.send(json.dumps({
            "type": "session.ready", "protocol": 1, "user_id": 7, "personas": ["navi"],
            "limits": {"max_text_chars": 500, "turns_per_minute": 20, "daily_tokens": 300000},
        }))
        async for raw in ws:
            msg = json.loads(raw)
            self.received.append(msg)
            tid = msg.get("turn_id")
            if msg["type"] == "turn.start":
                for out in self.script(msg):
                    if out == "stall":
                        # 생성 중에 멈춰 선다. 그동안 오는 메시지(취소)를 받아 처리한다 —
                        # 같은 핸들러라 바깥 루프는 이 사이에 못 읽는다.
                        nxt = json.loads(await ws.recv())
                        self.received.append(nxt)
                        if nxt["type"] == "turn.cancel":
                            self.cancelled.set()
                            await ws.send(json.dumps({"type": "turn.cancelled", "turn_id": tid}))
                        break
                    if out == "close":
                        await ws.close()
                        return
                    await ws.send(json.dumps({**out, "turn_id": tid}))
            elif msg["type"] == "turn.cancel":
                self.cancelled.set()
                await ws.send(json.dumps({"type": "turn.cancelled", "turn_id": tid}))
            elif msg["type"] == "turn.played":
                await ws.send(json.dumps({"type": "turn.confirmed", "turn_id": tid, "ok": True}))

    async def __aenter__(self):
        self._server = await serve(self._handler, "127.0.0.1", 0, process_request=self._auth)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self._server.close()
        await self._server.wait_closed()

    def of_type(self, kind: str) -> list[dict]:
        return [m for m in self.received if m["type"] == kind]


def make_link(server: FakeServer, tmp_path: Path, token: str = TOKEN) -> ServerLink:
    token_path = tmp_path / "server_token"
    token_path.write_text(token, encoding="utf-8")
    return ServerLink(f"http://127.0.0.1:{server.port}", token_path)


def request(text: str = "나 왔어", kind: TurnKind = TurnKind.REACTIVE) -> RemoteTurnRequest:
    return RemoteConductor(CARD, "navi").build_request(text, 1, "s", kind=kind)


async def drain(brain: RemoteBrain, req: RemoteTurnRequest) -> list[str]:
    return [t async for t in brain.generate_stream(req)]


# ── 조립은 서버 몫 ────────────────────────────────────────────────────────


def test_remote_conductor_sends_only_the_utterance_and_persona_key():
    conductor = RemoteConductor(CARD, "navi")

    req = conductor.build_request("안녕", 1, "s", kind=TurnKind.REACTIVE)

    assert req.system == "" and req.context == ""
    assert [m.text for m in req.messages] == ["안녕"]  # 이력·카드를 싣지 않는다
    assert req.persona == "navi"
    other = CharacterCard.load(Path(__file__).parents[1] / "personas" / "example_jp.yaml")
    conductor.set_card(other, "example_jp")
    assert conductor.card is other
    assert conductor.build_request("x", 1, "s").persona == "example_jp"


# ── 한 턴 ─────────────────────────────────────────────────────────────────


async def test_turn_streams_mood_as_leading_tag_then_body(tmp_path):
    async with FakeServer() as server:
        brain = RemoteBrain(make_link(server, tmp_path))

        tokens = await drain(brain, request("나 왔어"))

        # 서버의 구조화 무드를 파이프라인이 아는 선행 태그로 되돌린다.
        assert tokens == ["[mood:bright] ", "안녕.", " 왔어?"]
        assert brain.last_result == BrainResult("안녕. 왔어?", Usage(50, 10))
        start = server.of_type("turn.start")[0]
        assert start["kind"] == "reactive" and start["persona"] == "navi"
        assert start["text"] == "나 왔어"
        await brain._link.close()


async def test_pipeline_runs_unchanged_over_the_server(tmp_path):
    """파이프라인·TTS는 모드를 모른다 — 무드로 톤을 고르고, 태그는 소리로도 기억으로도 안 샌다."""
    async with FakeServer() as server:
        link = make_link(server, tmp_path)
        brain, mouth = RemoteBrain(link), FakeMouth()
        picked: list[str] = []
        pipeline = TurnPipeline(
            brain=brain, mouth=mouth, conductor=RemoteConductor(CARD, "navi"), voice=VOICE,
            mood_voice=lambda mood: picked.append(mood) or None,
        )

        result = await pipeline.run_turn("나 왔어", user_id=1, session_id="s")

        assert picked == ["bright"]
        assert mouth.spoken == ["안녕. 왔어?"]
        assert result.full_text == "안녕. 왔어?"
        await link.close()


# ── 재생 보고 (G1) ────────────────────────────────────────────────────────


async def test_full_playback_is_reported_as_whole(tmp_path):
    async with FakeServer() as server:
        brain = RemoteBrain(make_link(server, tmp_path))
        await drain(brain, request())

        await brain.report_played(None)  # 끊기지 않았으면 값을 안 본다

        assert server.of_type("turn.played")[0] | {"turn_id": None} == {
            "type": "turn.played", "turn_id": None, "played_chars": 7, "interrupted": False,
        }
        await brain._link.close()


async def test_barge_in_after_generation_reports_what_was_heard(tmp_path):
    async with FakeServer() as server:
        brain = RemoteBrain(make_link(server, tmp_path))
        await drain(brain, request())

        brain.cancel()  # 생성은 끝났고 재생 도중 사용자가 끊었다
        await brain.report_played(3)

        played = server.of_type("turn.played")[0]
        assert played["played_chars"] == 3 and played["interrupted"] is True
        assert not server.of_type("turn.cancel")  # 끝난 턴에 취소를 보내지 않는다
        await brain._link.close()


async def test_barge_in_with_unknown_position_reports_nothing_heard(tmp_path):
    """어디까지 들렸는지 모르면 0 — 못 들은 말을 기억하는 것보다 잊는 쪽이 덜 나쁘다."""
    async with FakeServer() as server:
        brain = RemoteBrain(make_link(server, tmp_path))
        await drain(brain, request())
        brain.cancel()

        await brain.report_played(None)

        assert server.of_type("turn.played")[0]["played_chars"] == 0
        await brain._link.close()


async def test_report_is_sent_once_and_clamped(tmp_path):
    async with FakeServer() as server:
        brain = RemoteBrain(make_link(server, tmp_path))
        await drain(brain, request())
        brain.cancel()

        await brain.report_played(999)
        await brain.report_played(1)

        assert [m["played_chars"] for m in server.of_type("turn.played")] == [7]
        await brain._link.close()


async def test_backend_reports_to_server_instead_of_writing_local_memory(tmp_path):
    async with FakeServer() as server:
        brain = RemoteBrain(make_link(server, tmp_path))
        await drain(brain, request())
        mouth = FakeMouth()

        class NoWrites:
            def __getattr__(self, name):
                raise AssertionError(f"연결 모드에서 로컬 기억을 건드렸다: {name}")

        backend = TurnBackend(brain=brain, conductor=None, link=brain._link)
        await backend.record(
            NoWrites(), "s", 1, user_text="나 왔어", result=brain.last_result, mouth=mouth
        )

        assert len(server.of_type("turn.played")) == 1
        await backend.close()


# ── 취소·끊김 (D22) ───────────────────────────────────────────────────────


async def test_cancel_during_generation_tells_the_server_and_yields_no_result(tmp_path):
    async with FakeServer() as server:
        server.script = lambda start: [
            {"type": "turn.accepted"},
            {"type": "turn.mood", "mood": "neutral"},
            {"type": "turn.delta", "seq": 0, "text": "길게"},
            "stall",
        ]
        brain = RemoteBrain(make_link(server, tmp_path))
        tokens: list[str] = []

        async for token in brain.generate_stream(request()):
            tokens.append(token)
            if token == "길게":
                brain.cancel()

        assert server.cancelled.is_set()
        assert tokens == ["[mood:neutral] ", "길게"]
        assert brain.last_result is None  # 서버가 폐기한 턴 — 호출부도 남기지 않는다
        await brain.report_played(None)
        assert not server.of_type("turn.played")
        await brain._link.close()


async def test_dropped_connection_fails_the_turn_and_reconnects_next_time(tmp_path):
    async with FakeServer() as server:
        server.script = lambda start: [{"type": "turn.accepted"}, "close"]
        brain = RemoteBrain(make_link(server, tmp_path))

        with pytest.raises(BrainUnavailable):
            await drain(brain, request())

        server.script = FakeServer().script
        assert await drain(brain, request()) == ["[mood:bright] ", "안녕.", " 왔어?"]
        assert server.connections == 2  # 이어받지 않고 새로 연다
        await brain._link.close()


async def test_missing_delta_fails_the_turn(tmp_path):
    async with FakeServer() as server:
        server.script = lambda start: [
            {"type": "turn.delta", "seq": 0, "text": "a"},
            {"type": "turn.delta", "seq": 2, "text": "c"},
        ]
        brain = RemoteBrain(make_link(server, tmp_path))

        with pytest.raises(BrainUnavailable, match="빠졌"):
            await drain(brain, request())
        await brain._link.close()


# ── 거절 ──────────────────────────────────────────────────────────────────


async def test_server_rejection_surfaces_code_and_retry(tmp_path):
    async with FakeServer() as server:
        server.script = lambda start: [
            {"type": "turn.error", "code": "rate_limited", "message": "너무 자주", "retry_after_s": 12},
        ]
        brain = RemoteBrain(make_link(server, tmp_path))

        with pytest.raises(ServerError) as info:
            await drain(brain, request())

        assert info.value.code == "rate_limited" and info.value.retry_after_s == 12
        # 두뇌가 답하지 못한 것으로 다룬다 — 데몬은 이 턴을 기억에 남기지 않는다.
        assert isinstance(info.value, BrainUnavailable)
        await brain._link.close()


async def test_rejected_token_is_an_auth_error_and_is_not_replaced(tmp_path):
    async with FakeServer() as server:
        link = make_link(server, tmp_path, token="navi_dt_wrong")

        with pytest.raises(BrainAuthError, match="토큰"):
            await link.connect()

        # 조용히 새로 등록하지 않는다 — 새 토큰은 새 사용자, 곧 기억 상실이다.
        assert (tmp_path / "server_token").read_text(encoding="utf-8") == "navi_dt_wrong"


async def test_unreachable_server_is_unavailable(tmp_path):
    (tmp_path / "server_token").write_text(TOKEN, encoding="utf-8")
    link = ServerLink("http://127.0.0.1:9", tmp_path / "server_token")

    with pytest.raises(BrainUnavailable):
        await link.connect()


# ── 기기 등록 ─────────────────────────────────────────────────────────────


def test_first_connection_registers_the_device_and_keeps_the_token(tmp_path):
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            hits.append(self.path)
            body = json.dumps({"device_token": "navi_dt_new", "user_id": 3, "device_id": 4})
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *args):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        token_path = tmp_path / "nested" / "server_token"
        link = ServerLink(f"http://127.0.0.1:{httpd.server_port}", token_path)

        assert link._token() == "navi_dt_new"
        assert token_path.read_text(encoding="utf-8") == "navi_dt_new"
        assert link._token() == "navi_dt_new"  # 두 번째는 파일에서 — 다시 등록하지 않는다
        assert hits == ["/v1/devices"]
    finally:
        httpd.shutdown()


# ── TTS의 재생 위치 ───────────────────────────────────────────────────────


async def test_supertonic_counts_only_sentences_played_to_the_end():
    from navi.mouth.supertonic import SupertonicMouth

    class Engine:
        sample_rate = 16000

    mouth = SupertonicMouth(tts=Engine())
    played: list[str] = []
    mouth._synth = lambda text, voice: text  # 합성 대신 문장을 그대로 넘긴다

    def play(sentence):
        played.append(sentence)
        if len(played) == 2:
            mouth._stopped = True  # 둘째 문장 재생 도중 끊겼다

    mouth._play = play

    async def tokens():
        for t in ["첫 문장이야. ", "둘째 문장이야. ", "셋째."]:
            yield t

    await mouth.speak_stream(tokens(), VOICE)

    assert played == ["첫 문장이야.", "둘째 문장이야."]
    # 끝까지 들린 건 첫 문장까지다. 문장 뒤 공백은 다음 문장 앞에 붙어 있어 세지 않는다 —
    # 서버가 이 위치에서 자르면 들린 문장이 정확히 남는다.
    assert mouth.played_chars == len("첫 문장이야.")


async def test_supertonic_counts_the_tail_without_terminator():
    from navi.mouth.supertonic import SupertonicMouth

    class Engine:
        sample_rate = 16000

    mouth = SupertonicMouth(tts=Engine())
    mouth._synth = lambda text, voice: text
    mouth._play = lambda wav: None

    async def tokens():
        for t in ["안녕. ", "왔어"]:
            yield t

    await mouth.speak_stream(tokens(), VOICE)

    assert mouth.played_chars == len("안녕. 왔어")


async def test_fake_mouth_counts_what_it_consumed():
    mouth = FakeMouth()

    async def tokens():
        for t in ["안녕", ". 왔어?"]:
            yield t

    await mouth.speak_stream(tokens(), VOICE)

    assert mouth.played_chars == len("안녕. 왔어?")
