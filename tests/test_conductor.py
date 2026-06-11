from dataclasses import replace
from pathlib import Path

from navi.brain import create_brain
from navi.brain.echo import EchoBrain
from navi.conductor import Conductor
from navi.config import BrainConfig, Config
from navi.memory import MemoryStore
from navi.persona import CharacterCard

CARD_PATH = Path(__file__).parents[1] / "personas" / "navi.yaml"

MODELS = {
    "gemini": "gemini-2.5-flash",
    "anthropic": "claude-haiku-4-5-20251001",
    "echo": "echo",
}


def make_config(tmp_path, vendor: str = "echo") -> Config:
    return Config(
        brain=BrainConfig(vendor=vendor, models=MODELS),
        db_path=tmp_path / "t.db",
        recent_turns=5,
        persona_card_path=CARD_PATH,
        gemini_api_key=None,
        anthropic_api_key=None,
    )


def make_conductor(config: Config) -> tuple[Conductor, MemoryStore, int]:
    store = MemoryStore(config.db_path)
    uid = store.ensure_user("친구")
    card = CharacterCard.load(config.persona_card_path)
    return Conductor(card=card, memory=store, config=config), store, uid


def test_build_request_assembles_persona_memory_trigger(tmp_path):
    config = make_config(tmp_path)
    conductor, store, uid = make_conductor(config)
    store.append_turn("지난세션", uid, "user", "어제 한 얘기")
    store.append_turn("지난세션", uid, "assistant", "응 들었어")

    request = conductor.build_request("오늘 트리거", user_id=uid, session_id="새세션")

    assert "성격과 말투 규칙" in request.system  # 페르소나
    assert [m.text for m in request.messages] == ["어제 한 얘기", "응 들었어", "오늘 트리거"]
    assert request.messages[-1].role == "user"  # 트리거는 항상 마지막 user 메시지
    assert request.model == "echo"


def test_recent_turns_window_respected(tmp_path):
    config = make_config(tmp_path)  # recent_turns=5
    conductor, store, uid = make_conductor(config)
    for i in range(10):
        store.append_turn("s", uid, "user", f"턴{i}")

    request = conductor.build_request("트리거", user_id=uid, session_id="s")
    # 최근 5턴 + 트리거 1
    assert [m.text for m in request.messages] == ["턴5", "턴6", "턴7", "턴8", "턴9", "트리거"]


def test_vendor_swap_changes_model_only(tmp_path):
    """Phase 1 완료 기준 2의 조립 레벨 검증 — 벤더를 바꿔도 인격(system·messages)은 동일."""
    config = make_config(tmp_path, vendor="gemini")
    conductor, store, uid = make_conductor(config)
    store.append_turn("s", uid, "user", "안녕")

    req_gemini = conductor.build_request("뭐해?", user_id=uid, session_id="s")
    swapped = Conductor(
        card=CharacterCard.load(config.persona_card_path),
        memory=store,
        config=replace(config, brain=replace(config.brain, vendor="anthropic")),
    )
    req_anthropic = swapped.build_request("뭐해?", user_id=uid, session_id="s")

    assert req_gemini.system == req_anthropic.system
    assert req_gemini.messages == req_anthropic.messages
    assert req_gemini.model != req_anthropic.model


def test_create_brain_echo_and_missing_key_error(tmp_path):
    assert isinstance(create_brain(make_config(tmp_path, "echo")), EchoBrain)
    try:
        create_brain(make_config(tmp_path, "gemini"))  # 키 없음 → 친절한 에러
        raise AssertionError("키 없이 GeminiBrain이 생성되면 안 된다")
    except RuntimeError as e:
        assert "GEMINI_API_KEY" in str(e)


async def test_e2e_with_echo_brain(tmp_path):
    """입력→조립→스트림→기억 적재까지 전체 경로 (키·네트워크 없이)."""
    config = make_config(tmp_path)
    conductor, store, uid = make_conductor(config)
    brain = create_brain(config)

    request = conductor.build_request("안녕 나비", user_id=uid, session_id="s1")
    tokens = [t async for t in brain.generate_stream(request)]
    result = brain.last_result

    assert "".join(tokens) == result.full_text == "(echo) 안녕 나비"
    store.append_turn("s1", uid, "user", "안녕 나비")
    store.append_turn("s1", uid, "assistant", result.full_text)
    store.log_usage("llm", result.usage)
    assert [t.text for t in store.recall_recent("s1", 10)] == ["안녕 나비", "(echo) 안녕 나비"]
