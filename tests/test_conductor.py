from dataclasses import replace
from datetime import time as dtime
from pathlib import Path

from navi.brain import create_brain
from navi.brain.echo import EchoBrain
from navi.conductor import Conductor
from navi.config import (
    BrainConfig,
    Config,
    ControlConfig,
    ModeConfig,
    MouthConfig,
    ProactiveConfig,
    WakeWordConfig,
)
from navi.memory import MemoryStore
from navi.models import TurnKind, VoiceProfile
from navi.persona import CharacterCard

CARD_PATH = Path(__file__).parents[1] / "personas" / "navi.yaml"

MODELS = {
    "gemini": "gemini-2.5-flash",
    "anthropic": "claude-haiku-4-5-20251001",
    "echo": "echo",
}


def make_config(tmp_path, vendor: str = "echo") -> Config:
    return Config(
        root=tmp_path,
        brain=BrainConfig(vendor=vendor, models=MODELS),
        mouth=MouthConfig(
            vendor="fake",
            voice=VoiceProfile(name="navi", vendor_voice_id="stub"),
            options={},
        ),
        wakeword=WakeWordConfig(
            engine="openwakeword",
            keywords=(),
            owww_model_path=None,
            owww_model_name="hey_jarvis",
            threshold=0.5,
            vad_threshold=0.0,
            vosk_model_path=None,
            access_key=None,
            keyword_path=None,
            model_path=None,
            sensitivity=0.5,
            active_timeout_ms=30000,
        ),
        mode=ModeConfig(
            sleep_start=dtime(23, 0), sleep_end=dtime(7, 0), snooze_minutes=30
        ),
        proactive=ProactiveConfig(
            base_interval_s=3600,
            min_gap_s=1800,
            daily_cap=8,
            hazard_shape_k=2.0,
            time_weights={"morning": 1.2, "afternoon": 1.0, "evening": 1.1, "night": 0.5},
        ),
        control=ControlConfig(enabled=False, port=8765),  # 유닛에선 서버 미기동
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


def test_reactive_kind_is_bare_trigger(tmp_path):
    """REACTIVE(및 기본값)는 트리거를 그대로 마지막 user 메시지로 싣는다 — 기존 거동."""
    config = make_config(tmp_path)
    conductor, _store, uid = make_conductor(config)

    default = conductor.build_request("트리거", user_id=uid, session_id="s")
    explicit = conductor.build_request(
        "트리거", user_id=uid, session_id="s", kind=TurnKind.REACTIVE
    )
    assert default.messages[-1] == explicit.messages[-1]
    assert default.messages[-1].role == "user"
    assert default.messages[-1].text == "트리거"  # 감싸지 않음


def test_proactive_kind_wraps_trigger(tmp_path):
    """PROACTIVE는 소재를 프레이밍으로 감싼다 — 트리거는 포함하되 그 자체는 아님."""
    config = make_config(tmp_path)
    conductor, _store, uid = make_conductor(config)

    request = conductor.build_request(
        "○○팀이 3대1로 이겼다", user_id=uid, session_id="s", kind=TurnKind.PROACTIVE
    )
    last = request.messages[-1]
    assert last.role == "user"
    assert "○○팀이 3대1로 이겼다" in last.text  # 소재는 그대로 실림
    assert last.text != "○○팀이 3대1로 이겼다"  # 하지만 프레이밍으로 감싸짐
    assert "먼저" in last.text  # "네가 먼저 말을 꺼내줘" 지시


def test_proactive_frame_forbids_inventing_a_source(tmp_path):
    """실측(turn_assembly §4.1 ①)에서 두뇌가 "어제 뉴스에서 봤는데"로 빈칸을 메웠다.

    프레임은 **금지만** 말한다. "어떻게 알게 됐는가"는 카드 소유다(아래 테스트 참고).
    """
    config = make_config(tmp_path)
    conductor, _store, uid = make_conductor(config)

    request = conductor.build_request(
        "○○팀이 이겼다", user_id=uid, session_id="s", kind=TurnKind.PROACTIVE
    )
    assert "지어내지" in request.messages[-1].text


def test_proactive_frame_carries_no_persona_worldview(tmp_path):
    """프레임은 모든 카드가 공유하는 코드라 특정 세계관을 넣으면 안 된다.

    회귀 방지: 한때 "집 안에 흘러든 이야기로 알게 된 거야"라고 나비의 설정을 박아,
    집과 무관한 카드(example_jp 등)가 남의 세계관을 뒤집어썼다. 경로는 카드 소유다 —
    카드가 안 주면 출처 없이 사실만 말하게 되고 그건 정상 폴백이다.
    """
    config = make_config(tmp_path)
    conductor, _store, uid = make_conductor(config)

    text = conductor.build_request(
        "소재", user_id=uid, session_id="s", kind=TurnKind.PROACTIVE
    ).messages[-1].text

    for worldview in ("집 안", "흘러든", "스피커", "정령"):
        assert worldview not in text


def test_callback_kind_frames_material_as_the_users_own_words(tmp_path):
    """콜백은 뉴스 프레임을 그대로 쓸 수 없다 — 두 군데가 정반대다.

    ①"사용자가 알려준 게 아니야"는 콜백에선 거짓이고 ②뉴스는 되묻지 말아야 하지만
    콜백은 되묻는 것이 목적이다.
    """
    config = make_config(tmp_path)
    conductor, _store, uid = make_conductor(config)

    news = conductor.build_request(
        "소재", user_id=uid, session_id="s", kind=TurnKind.PROACTIVE
    ).messages[-1].text
    callback = conductor.build_request(
        "사용자가 이직을 고민한다고 말했다",
        user_id=uid,
        session_id="s",
        kind=TurnKind.PROACTIVE_CALLBACK,
    ).messages[-1].text

    assert callback != news
    assert "사용자가 이직을 고민한다고 말했다" in callback  # 소재는 그대로 실린다
    assert "전에 한 말" in callback  # 출처가 사용자 자신임을 밝힌다
    assert "되묻지 말고" not in callback  # 콜백은 되묻는 게 목적
    assert "되묻지 말고" in news  # 뉴스는 반대


def test_kind_does_not_change_system(tmp_path):
    """핵심 불변식 — 모든 kind의 system(카드 코어)이 바이트 단위로 동일(캐시 prefix 불변)."""
    config = make_config(tmp_path)
    conductor, _store, uid = make_conductor(config)

    systems = {
        conductor.build_request("x", user_id=uid, session_id="s", kind=kind).system
        for kind in TurnKind
    }
    assert len(systems) == 1


def test_set_card_swaps_persona_from_next_request(tmp_path):
    """페르소나 카드 교체(Stage 15-②) — 다음 build_request의 system이 새 카드로.

    aris.yaml은 gitignore(저작권 로컬 전용)라 인라인 미니 카드로 검증한다.
    """
    config = make_config(tmp_path)
    conductor, _store, uid = make_conductor(config)
    assert "나비" in conductor.build_request("x", user_id=uid, session_id="s").system

    other = tmp_path / "other.yaml"
    other.write_text(
        "character: 다른애\n"
        "profiles:\n"
        "  - {name: 기본, min_intimacy: 0, background: 배경, traits: 성격,\n"
        "     example_dialogues: [{user: u, assistant: a}]}\n",
        encoding="utf-8",
    )
    card = CharacterCard.load(other)
    conductor.set_card(card)
    assert conductor.card is card
    request = conductor.build_request("y", user_id=uid, session_id="s")
    assert "다른애" in request.system
    assert "나비" not in request.system.split("\n")[0]  # 첫 줄(정체성 선언)이 교체됨


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

    assert "".join(tokens) == result.full_text == "안녕 나비"
    store.append_turn("s1", uid, "user", "안녕 나비")
    store.append_turn("s1", uid, "assistant", result.full_text)
    store.log_usage("llm", result.usage)
    assert [t.text for t in store.recall_recent("s1", 10)] == ["안녕 나비", "안녕 나비"]
