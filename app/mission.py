"""로봇 미션 진행 상태. 로봇 쪽(IAMSSORRY/Piper mission.py)이 POST /ingest/mission 으로 이벤트를 보낸다.

판정 / 모션 말고도 대시보드가 실시간으로 보여야 하는 것들이다:
진행(몇 번째 사과, 지금 무슨 동작), 파지 실패 / 건너뜀, 비상정지, 적응형 조정 상태.

진행 중(running)인데 MISSION_STALE_S 동안 로봇 이벤트가 없거나 로봇팔이 끊겨 있으면 서버가 스스로
"stalled" 로 바꾼다(app/main.py 의 감시 태스크). 그 뒤 로봇 이벤트가 다시 오면 running 으로 돌아간다.

이벤트 하나마다 상태를 갱신하고 `/ws/judge` 로 {"type": "mission", "event": ..., "state": ...} 를 보낸다.
알 수 없는 이벤트도 버리지 않고 저장 / 전달한다(로봇 쪽이 먼저 새 이벤트를 보내도 깨지지 않게).
"""

import time

# 이벤트 → 그 이벤트가 받는 필드. 여기 없는 필드는 무시한다.
EVENTS = {
    "start": ("apple_count", "sim"),                     # 미션 시작
    "apple": ("index", "total"),                         # n 번째 사과 시작 (1부터)
    "phase": ("phase",),                                 # pick / inspect / place / home / nudge / estop_return / estop_rest
    "pick": ("ok", "attempt", "width_mm"),               # 파지 결과 (attempt 0 = 첫 시도)
    "skip": ("index", "reason"),                         # 그 사과 건너뜀 (도달 불가 등)
    "adaptive": ("scale", "release_h", "frozen", "down_streak"),  # 적응형 조정 상태
    "estop": ("reason",),                                # 비상정지 / 고장
    "end": ("duration_s", "results"),                    # 미션 끝
    "stalled": ("idle_s", "reason"),                     # (서버가 만든다) 진행 중인데 로봇 소식이 끊김
}


class MissionState:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.status = "idle"            # idle / running / stalled / finished / estop
        self.sim: bool | None = None
        self.apple_count: int | None = None
        self.apple_index: int | None = None
        self.phase: str | None = None
        self.picks_ok = 0
        self.picks_failed = 0
        self.skipped = 0
        self.adaptive: dict | None = None
        self.estop_reason: str | None = None
        self.started_at: float | None = None
        self.ended_at: float | None = None
        self.duration_s: float | None = None
        self.updated_at: float | None = None
        # 마지막 로봇 이벤트를 **서버가 받은** 시각. 멈춤 판단은 로봇 PC 시계(ts)가 아니라 이것으로 한다.
        self.last_event_at: float | None = None

    def new_run(self) -> None:
        """통계 초기화(새 회차) 때 부른다.

        회차별 집계(파지 성공 / 실패, 건너뜀)는 0 으로 되돌린다. 로봇 미션이 진행 중이면 로봇은 계속
        움직이므로 진행 상태(status, apple_count, apple_index, phase, adaptive 등)는 그대로 두고,
        진행 중이 아니면(idle / finished / estop) 전체를 idle 초기값으로 되돌린다.
        """
        if self.status not in ("running", "stalled"):
            self.reset()
            return
        self.picks_ok = 0
        self.picks_failed = 0
        self.skipped = 0
        self.updated_at = time.time()

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "sim": self.sim,
            "apple_count": self.apple_count,
            "apple_index": self.apple_index,
            "phase": self.phase,
            "picks_ok": self.picks_ok,
            "picks_failed": self.picks_failed,
            "skipped": self.skipped,
            "adaptive": self.adaptive,
            "estop_reason": self.estop_reason,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_s": self.duration_s,
            "updated_at": self.updated_at,
        }

    def apply(self, data: dict) -> dict:
        """이벤트 하나를 반영하고, 저장 / 전달할 정리된 이벤트를 돌려준다. 형식이 틀리면 ValueError."""
        name = data.get("event")
        if not isinstance(name, str) or not name:
            raise ValueError("event 가 필요하다")
        ts = float(data.get("ts") or time.time())
        fields = EVENTS.get(name)
        payload = {k: data[k] for k in fields if k in data} if fields else {
            k: v for k, v in data.items() if k not in ("event", "ts")
        }

        # 멈춤(stalled) 뒤에 로봇 이벤트가 다시 오면 진행 중으로 되돌린다
        if self.status == "stalled" and name not in ("stalled", "start", "estop", "end"):
            self.status = "running"
        if name != "stalled":
            self.last_event_at = time.time()

        if name == "start":
            self.reset()
            self.status = "running"
            self.started_at = ts
            self.apple_count = _int(payload.get("apple_count"))
            self.sim = payload.get("sim")
        elif name == "apple":
            self.status = "running"
            self.apple_index = _int(payload.get("index"))
            if payload.get("total") is not None:
                self.apple_count = _int(payload["total"])
        elif name == "phase":
            self.phase = str(payload.get("phase") or "") or None
        elif name == "pick":
            if payload.get("ok"):
                self.picks_ok += 1
            else:
                self.picks_failed += 1
        elif name == "skip":
            self.skipped += 1
        elif name == "adaptive":
            self.adaptive = payload
        elif name == "estop":
            self.status = "estop"
            self.estop_reason = str(payload.get("reason") or "비상정지")
            self.phase = None
            self.ended_at = ts
        elif name == "stalled":
            # 어디서 멈췄는지 보여야 하므로 나머지 필드는 그대로, 정상 종료가 아니므로 ended_at 도 비워 둔다
            self.status = "stalled"
        elif name == "end":
            self.status = "finished"
            self.phase = None
            self.ended_at = ts
            self.duration_s = payload.get("duration_s")

        self.updated_at = ts
        return {"event": name, "ts": ts, **payload}


def stall_reason(state: MissionState, arm_down_s: float | None, stale_s: float, now: float) -> tuple[str, float] | None:
    """진행 중인 미션이 멈췄는지. 멈췄으면 (이유, 마지막 이벤트 이후 초)."""
    if state.status != "running" or state.last_event_at is None:
        return None
    idle_s = now - state.last_event_at
    if idle_s >= stale_s:
        return f"로봇에서 {stale_s:.0f}초 동안 이벤트가 없습니다", idle_s
    if arm_down_s is not None and arm_down_s >= stale_s:
        return "로봇팔 연결이 끊겨 미션이 멈췄습니다", idle_s
    return None


def _int(value) -> int | None:
    return None if value is None else int(value)
