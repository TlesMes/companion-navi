"""연결 모드 — 턴을 서버(server/)에 맡긴다 (design/service.md §2.3, design/protocol.md).

단독 모드에서는 데몬이 요청을 조립하고 자기 키로 두뇌를 부른다. 연결 모드에서는 발화 텍스트와
턴 종류만 서버로 보내고, 조립·두뇌 호출·대화 저장은 서버가 한다. 마이크·STT·게이트·TTS는
어느 모드든 이 기기에 남는다.
"""

from navi.remote.brain import RemoteBrain, RemoteConductor, RemoteTurnRequest
from navi.remote.link import ServerError, ServerLink

__all__ = [
    "RemoteBrain",
    "RemoteConductor",
    "RemoteTurnRequest",
    "ServerError",
    "ServerLink",
]
