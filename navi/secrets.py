"""비밀의 자리 — .env 한 줄만 갈아끼운다.

**왜 .env이고 navi.db가 아닌가.** API 키는 벤더사에 원문으로 보내야 해서 해싱이 불가능하다.
그런데 navi.db는 나비의 기억이라 백업·이관·디버깅 때 통째로 복사하는 파일이다 — 키가 그
안에 있으면 "기억 파일 하나 보내줘"가 곧 키 유출이다. .env는 gitignore된 단일 목적 파일이고
이미 그 역할을 하고 있다(2026.08.31 결정, keyring은 의존성·헤드리스 경로 부담으로 보류).

**왜 통째로 안 덮는가.** .env엔 주석과 다른 벤더의 키가 같이 산다. GUI에서 키 하나 바꿨다고
나머지가 날아가면 안 된다 — 그래서 줄 단위 부분 갱신이고, 쓰다 죽어도 원본이 남도록
임시 파일 → os.replace로 원자 교체한다.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# `NAME=` 형태의 대입 줄. 앞쪽 공백과 dotenv의 `export ` 접두사를 허용한다 —
# python-dotenv가 읽어 주는 형태라 우리도 같은 줄을 갱신 대상으로 봐야 한다.
def _assignment(name: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*(?:export\s+)?{re.escape(name)}\s*=")


def write_env_key(root: Path, name: str, value: str) -> Path:
    """.env의 `name` 줄만 `value`로 교체 — 없으면 추가, 파일이 없으면 생성.

    주석·빈 줄·다른 키를 그대로 보존한다. 반환은 쓴 파일 경로.
    값은 로그에 남기지 않는다(호출부도 남기지 말 것).
    """
    if "\n" in value or "\r" in value:
        # 개행이 통과하면 값 하나로 다른 키 줄을 새로 심을 수 있다.
        raise ValueError(f"{name} 값에 개행이 있습니다 — 저장하지 않았어요")

    path = root / ".env"
    line = f"{name}={value}"
    try:
        original = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        original = ""

    pattern = _assignment(name)
    lines = original.splitlines()
    for i, existing in enumerate(lines):
        if pattern.match(existing):
            lines[i] = line
            break
    else:
        lines.append(line)

    # 임시 파일 → os.replace. 같은 디렉터리에 만들어야 replace가 원자적이다.
    tmp = path.with_name(f".env.{os.getpid()}.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def mask_key(value: str | None) -> str:
    """표시용 마스킹 — 키를 가진 게 맞는지 사람이 알아볼 만큼만 남긴다.

    앞 6자는 sk-ant·AIzaSy 같은 발급처 접두사라 비밀이 아니고, 뒤 4자는 "내가 넣은 그
    키"를 식별하는 데 쓴다. 짧은 값은 통째로 가린다(전부 노출될 수 있어서).
    """
    if not value:
        return ""
    if len(value) < 16:
        return "****"
    return f"{value[:6]}****{value[-4:]}"
