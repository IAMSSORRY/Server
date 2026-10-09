"""PIPER Studio 의 로봇팔 상태를 주기적으로 읽는다. **읽기만 한다** — 연결 / 모드 / 토크는 건드리지 않는다.

팔을 다시 연결하면 PIPER 가 슬레이브 설정과 토크 OFF 를 하므로, 사람이 없는 사이 자동으로
하면 팔이 힘을 잃을 수 있다. 그래서 복구는 사람이 PIPER 화면에서 하고, 이 서버는 끊긴 걸 알리기만 한다.
"""

import asyncio
import logging
import time

import httpx

log = logging.getLogger(__name__)

# PIPER 의 /api/robots/current 는 팔마다 CAN 버스를 잠깐(약 0.35초) 듣는다. 너무 자주 부르지 않는다.
POLL_INTERVAL_S = 3.0


class ArmStatus:
    def __init__(self, source: str) -> None:
        self.source = source            # "piper" | "mock" | "none"
        self.ok: bool | None = None     # None = 아직 모름 / 감시하지 않음
        self.message: str | None = None
        self.arms: list[dict] = []
        self.checked_at: float | None = None
        self.down_since: float | None = None   # ok 가 false 가 된 시각 (멈춘 미션 판단용)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "ok": self.ok,
            "message": self.message,
            "arms": self.arms,
            "checked_at": self.checked_at,
        }

    def update(self, ok: bool, message: str | None, arms: list[dict]) -> None:
        if ok != self.ok:
            if ok:
                log.info("로봇팔 정상")
            else:
                log.warning("로봇팔 이상: %s", message)
        now = time.time()
        if not ok and self.down_since is None:
            self.down_since = now
        elif ok:
            self.down_since = None
        self.ok, self.message, self.arms = ok, message, arms
        self.checked_at = now

    @property
    def down_for(self) -> float | None:
        """로봇팔이 끊긴 채로 지난 초. 정상이거나 감시하지 않으면 None."""
        return None if self.down_since is None else time.time() - self.down_since


def _summarize(arm: dict) -> dict:
    return {k: arm.get(k) for k in ("iface", "role", "connected", "responding", "state", "ready", "transport")}


def judge_arms(arms: list[dict]) -> tuple[bool, str | None]:
    """등록된(ready) 팔이 모두 연결 / 응답 / UP 이면 정상."""
    registered = [a for a in arms if a.get("ready")]
    if not registered:
        return False, "PIPER 에 등록된 로봇팔이 없다"
    for arm in registered:
        name = arm.get("iface") or "?"
        if arm.get("state") not in (None, "UP"):
            return False, f"로봇팔 {name} 의 CAN 인터페이스가 꺼져 있다 ({arm.get('state')}) — USB-CAN 어댑터를 확인하세요"
        if not arm.get("connected"):
            return False, f"로봇팔 {name} 가 연결돼 있지 않다 — PIPER 로봇 페이지에서 다시 연결하세요"
        if arm.get("responding") is False:
            return False, f"로봇팔 {name} 가 응답하지 않는다 — 전원과 CAN 케이블을 확인하세요"
    return True, None


async def watch(status: ArmStatus, base_url: str) -> None:
    async with httpx.AsyncClient(timeout=5.0) as client:
        while True:
            try:
                response = await client.get(f"{base_url}/api/robots/current")
                response.raise_for_status()
                arms = response.json().get("arms", [])
                ok, message = judge_arms(arms)
                status.update(ok, message, [_summarize(a) for a in arms])
            except asyncio.CancelledError:
                raise
            except Exception as e:
                status.update(False, f"PIPER Studio 에서 로봇팔 상태를 읽지 못했다 ({type(e).__name__})", [])
            await asyncio.sleep(POLL_INTERVAL_S)
