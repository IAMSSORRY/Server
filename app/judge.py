"""판정 / 모션 이벤트의 누적 통계, 이력, 웹소켓 구독자를 관리한다.

카메라 프레임과 달리 판정 이벤트는 하나도 빠지면 안 되므로 구독자마다 큐를 둔다.
이벤트는 /ingest 엔드포인트(LeRobot 쪽 프로세스)나 MOCK 태스크에서 들어오며,
상태 변경과 브로드캐스트는 모두 이벤트 루프 안에서만 일어난다.

기록은 SQLite(app/db.py)에도 남긴다. 메모리 상태(통계, recent, 다음 id)는 현재 회차(run)의 것이고,
서버가 다시 뜨면 끝나지 않은 회차를 DB 에서 읽어 복원한다. DB 쓰기는 기다리지 않으며,
실패해도 브로드캐스트는 계속된다.
"""

import asyncio
import logging
import time
from collections import deque

from app.db import Store
from app.mission import MissionState, stall_reason

log = logging.getLogger(__name__)

GRADES = ("상", "중")
HISTORY_MAX = 10_000
# 구독자 큐가 이만큼 밀리면 끊긴 클라이언트로 보고 연결을 닫는다. 이벤트를 버리지는 않는다.
QUEUE_MAX = 1_000


class SubscriberOverflow(Exception):
    pass


class Subscriber:
    """웹소켓 하나에 대응하는 이벤트 큐."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=QUEUE_MAX)
        self._overflow = asyncio.Event()

    def put(self, message: dict) -> bool:
        try:
            self._queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            self._overflow.set()
            return False

    async def get(self) -> dict:
        """다음 이벤트. 큐가 넘쳤다면 SubscriberOverflow 를 던진다."""
        if self._overflow.is_set():
            raise SubscriberOverflow
        get_task = asyncio.ensure_future(self._queue.get())
        overflow_task = asyncio.ensure_future(self._overflow.wait())
        done, pending = await asyncio.wait({get_task, overflow_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        if get_task in done:
            return get_task.result()
        raise SubscriberOverflow


class JudgeHub:
    def __init__(self, default_cam: str, store: Store) -> None:
        self._default_cam = default_cam
        self._store = store
        self._subscribers: set[Subscriber] = set()
        self.mission = MissionState()
        self._run_id = 0
        self._reset_state()

    async def open(self) -> None:
        """DB 를 열고 끝나지 않은 회차를 이어 쓴다. 서버 시작 때 한 번 부른다.

        cycle_time 은 복원하지 않는다 — 재시작 사이의 공백이 섞이므로 다음 판정부터 다시 잰다.
        """
        self._run_id, entries = await self._store.open()
        self._reset_state()
        for entry in entries:
            self._counts[entry["grade"]] = self._counts.get(entry["grade"], 0) + 1
            self._history.append(entry)
        if entries:
            self._next_id = entries[-1]["id"] + 1

    @property
    def run_id(self) -> int:
        return self._run_id

    def _reset_state(self) -> None:
        self._next_id = 1
        self._counts = {grade: 0 for grade in GRADES}
        self._history: deque[dict] = deque(maxlen=HISTORY_MAX)
        self._cycle_time: float | None = None
        self._last_judge_ts: float | None = None

    # ---- 이벤트 입력 (이벤트 루프 안에서만 호출) ----

    def add_judge(self, data: dict) -> dict:
        """판정 하나를 기록하고 브로드캐스트한다. 형식이 틀리면 ValueError."""
        try:
            grade = data["grade"]
            if grade not in GRADES:
                raise ValueError(f"알 수 없는 grade: {grade!r}")
            ts = float(data.get("ts") or time.time())
            judge = {
                "type": "judge",
                "id": self._next_id,
                "grade": grade,
                "confidence": float(data["confidence"]),
                "v_value": data["v_value"],
                "threshold": data["threshold"],
                "bbox": list(data["bbox"]),
                # bbox 가 어느 카메라 프레임 기준인지. 안 주면 기본 카메라.
                "cam": str(data.get("cam") or self._default_cam),
                "ts": ts,
            }
            # 선택: 판정의 추가 근거 (예: 흠 비율과 그 상한). 보냈을 때만 메시지에 실린다.
            if data.get("extra") is not None:
                if not isinstance(data["extra"], dict):
                    raise ValueError("extra 는 객체여야 한다")
                judge["extra"] = data["extra"]
        except (KeyError, TypeError) as e:
            raise ValueError(f"판정 형식 오류: {e}") from e

        self._next_id += 1
        self._counts[grade] += 1

        # 비전 노드가 cycle_time 을 주면 그대로 쓰고, 없으면 직전 판정과의 간격으로 계산한다.
        if data.get("cycle_time") is not None:
            self._cycle_time = float(data["cycle_time"])
        elif self._last_judge_ts is not None:
            self._cycle_time = round(ts - self._last_judge_ts, 3)
        self._last_judge_ts = ts

        self._history.append({
            "id": judge["id"],
            "grade": grade,
            "confidence": judge["confidence"],
            "v_value": judge["v_value"],
            "threshold": judge["threshold"],
            "ts": ts,
            "roll_detected": None,
        })
        self._store.submit(self._store.insert_judge, self._run_id, judge)

        self._broadcast(judge)
        self._broadcast(self._stats_message())
        return judge

    def add_motion(self, data: dict) -> dict:
        """모션 이벤트 하나를 기록하고 브로드캐스트한다. 형식이 틀리면 ValueError."""
        try:
            motion = {
                "type": "motion",
                "approach_speed": float(data["approach_speed"]),
                "place_height": float(data["place_height"]),
                "roll_detected": bool(data["roll_detected"]),
                "ts": float(data.get("ts") or time.time()),
            }
        except (KeyError, TypeError) as e:
            raise ValueError(f"모션 형식 오류: {e}") from e

        # 모션은 판정된 물체를 옮긴 결과다. id 가 있으면 그 판정에,
        # 없으면 아직 모션이 붙지 않은 가장 최근 판정에 roll_detected 를 기록한다.
        target_id = data.get("id")
        matched_id = None
        for entry in reversed(self._history):
            if entry["id"] == target_id or (target_id is None and entry["roll_detected"] is None):
                entry["roll_detected"] = motion["roll_detected"]
                matched_id = entry["id"]
                break
        self._store.submit(self._store.insert_motion, self._run_id, matched_id, motion)

        self._broadcast(motion)
        return motion

    def add_mission(self, data: dict) -> dict:
        """미션 이벤트 하나를 반영하고 {"type": "mission", "event", "state"} 를 브로드캐스트한다."""
        event = self.mission.apply(data)
        self._store.submit(self._store.insert_event, self._run_id, event)
        message = self.mission_message(event)
        self._broadcast(message)
        return message

    def check_stalled(self, arm_down_s: float | None, stale_s: float) -> dict | None:
        """진행 중 미션이 멈췄으면 stalled 이벤트를 기록 / 브로드캐스트한다. 감시 태스크가 1초마다 부른다."""
        found = stall_reason(self.mission, arm_down_s, stale_s, time.time())
        if found is None:
            return None
        reason, idle_s = found
        return self.add_mission({"event": "stalled", "ts": time.time(), "idle_s": round(idle_s, 1), "reason": reason})

    def mission_message(self, event: dict | None = None) -> dict:
        return {"type": "mission", "event": event, "state": self.mission.to_dict()}

    def _broadcast(self, message: dict) -> None:
        for sub in list(self._subscribers):
            if not sub.put(message):
                # 이벤트를 몰래 버리는 대신 연결을 끊어서, 클라이언트가 재연결 후 snapshot 으로 맞추게 한다.
                self._subscribers.discard(sub)
                log.warning("구독자 큐가 가득 차서 연결을 끊는다")

    def _stats_message(self) -> dict:
        return {"type": "stats", "stats": self.stats(), "cycle_time": self._cycle_time}

    # ---- 웹 핸들러용 ----

    def stats(self) -> dict:
        return {**self._counts, "total": sum(self._counts.values())}

    @property
    def cycle_time(self) -> float | None:
        return self._cycle_time

    def history(self, limit: int | None = None) -> list[dict]:
        items = list(self._history)
        return items[-limit:] if limit else items

    def snapshot(self, limit: int = 50) -> dict:
        return {
            "type": "snapshot",
            "stats": self.stats(),
            "cycle_time": self._cycle_time,
            "recent": self.history(limit),
        }

    def reset(self) -> None:
        """통계 초기화 = 현재 회차를 닫고 새 회차를 연다. 기록은 DB 에 남는다."""
        ending, self._run_id = self._run_id, self._run_id + 1
        self._store.submit(self._store.start_run, ending, self._run_id, time.time())
        self._reset_state()
        self.mission.new_run()
        # 열려 있는 화면도 0 으로 맞추도록 snapshot 을 다시 보낸다.
        # 연결 직후와 같은 순서(snapshot → mission)로 미션 상태도 한 번 보낸다.
        self._broadcast(self.snapshot())
        self._broadcast(self.mission_message())

    def subscribe(self) -> tuple[Subscriber, dict, dict]:
        """구독을 등록하고 그 시점의 snapshot 과 미션 상태 메시지를 돌려준다.

        await 없이 한 번에 처리하므로 snapshot 과 이후 이벤트 사이에 빠지는 것이 없다.
        """
        sub = Subscriber()
        self._subscribers.add(sub)
        return sub, self.snapshot(), self.mission_message()

    def unsubscribe(self, sub: Subscriber) -> None:
        self._subscribers.discard(sub)
