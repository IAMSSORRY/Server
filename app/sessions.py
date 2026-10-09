"""로그인 없는 세션. 쿠키에 담긴 랜덤 ID 로 브라우저를 구분한다.

메모리에만 들고 있으므로 서버가 재시작되면 세션은 모두 새로 만들어진다.
"""

import secrets
import time
from dataclasses import dataclass, field

COOKIE_NAME = "ssorry_sid"
IDLE_TTL_SECONDS = 60 * 60


@dataclass
class Session:
    id: str
    created_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    connections: int = 0


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def get(self, session_id: str | None) -> Session | None:
        session = self._sessions.get(session_id) if session_id else None
        if session is not None:
            session.last_seen = time.time()
        return session

    def create(self) -> Session:
        self._purge_idle()
        session = Session(id=secrets.token_urlsafe(16))
        self._sessions[session.id] = session
        return session

    def all(self) -> list[Session]:
        return list(self._sessions.values())

    def _purge_idle(self) -> None:
        cutoff = time.time() - IDLE_TTL_SECONDS
        for sid, s in list(self._sessions.items()):
            if s.connections == 0 and s.last_seen < cutoff:
                del self._sessions[sid]
