"""판정 / 모션 이벤트의 누적 통계, 이력, 웹소켓 구독자를 관리한다.

카메라 프레임과 달리 판정 이벤트는 하나도 빠지면 안 되므로 구독자마다 큐를 둔다.
ROS 콜백은 spin 스레드에서 불리므로 call_soon_threadsafe 로 이벤트 루프에 넘기고,
상태 변경과 브로드캐스트는 모두 이벤트 루프 안에서만 일어난다.
"""

import asyncio
import json
import logging
import time
from collections import deque

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
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[Subscriber] = set()
        self._reset_state()

    def _reset_state(self) -> None:
        self._next_id = 1
        self._counts = {grade: 0 for grade in GRADES}
        self._history: deque[dict] = deque(maxlen=HISTORY_MAX)
        self._cycle_time: float | None = None
        self._last_judge_ts: float | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # ---- ROS spin 스레드에서 호출 ----

    def push_judge(self, raw: str) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._on_judge, raw)

    def push_motion(self, raw: str) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._on_motion, raw)

    # ---- 이벤트 루프 안에서만 호출 ----

    def _on_judge(self, raw: str) -> None:
        try:
            data = json.loads(raw)
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
                "ts": ts,
            }
        except (ValueError, KeyError, TypeError) as e:
            log.warning("판정 메시지 무시: %s (%s)", raw, e)
            return

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

        self._broadcast(judge)
        self._broadcast(self._stats_message())

    def _on_motion(self, raw: str) -> None:
        try:
            data = json.loads(raw)
            motion = {
                "type": "motion",
                "approach_speed": float(data["approach_speed"]),
                "place_height": float(data["place_height"]),
                "roll_detected": bool(data["roll_detected"]),
                "ts": float(data.get("ts") or time.time()),
            }
        except (ValueError, KeyError, TypeError) as e:
            log.warning("모션 메시지 무시: %s (%s)", raw, e)
            return

        # 모션은 판정된 물체를 옮긴 결과다. id 가 있으면 그 판정에,
        # 없으면 아직 모션이 붙지 않은 가장 최근 판정에 roll_detected 를 기록한다.
        target_id = data.get("id")
        for entry in reversed(self._history):
            if entry["id"] == target_id or (target_id is None and entry["roll_detected"] is None):
                entry["roll_detected"] = motion["roll_detected"]
                break

        self._broadcast(motion)

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
        self._reset_state()
        # 열려 있는 화면도 0 으로 맞추도록 snapshot 을 다시 보낸다.
        self._broadcast(self.snapshot())

    def subscribe(self) -> tuple[Subscriber, dict]:
        """구독을 등록하고 그 시점의 snapshot 을 돌려준다.

        await 없이 한 번에 처리하므로 snapshot 과 이후 이벤트 사이에 빠지는 것이 없다.
        """
        sub = Subscriber()
        self._subscribers.add(sub)
        return sub, self.snapshot()

    def unsubscribe(self, sub: Subscriber) -> None:
        self._subscribers.discard(sub)
