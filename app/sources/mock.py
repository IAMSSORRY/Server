"""MOCK=1 일 때 로봇과 카메라 대신 프레임과 이벤트를 만드는 개발용 태스크.

카메라마다 단색 배경 위에 원 몇 개를 그린 JPEG 를 만들고,
판정의 bbox 는 기본 카메라 JPEG 에 실제로 그려진 원의 위치와 맞춘다.
판정 / 모션 / 미션 이벤트는 /ingest 로 들어오는 것과 같은 형식으로 JudgeHub 에 넣는다.
미션은 사과 10개를 차례로 처리하고(약 10% 는 파지 실패로 건너뜀) 끝나면 3초 뒤 다시 시작한다.
"""

import asyncio
import io
import math
import random
import time

from PIL import Image, ImageDraw

from app.judge import JudgeHub
from app.stream import CameraHubs

WIDTH, HEIGHT = 640, 480
FPS = 10
MOTION_DELAY = 1.5  # 판정 후 모션 이벤트까지
# 실제 장비(IAMSSORRY/Piper config.yaml grade)와 같은 기준: v_value = 빨강 비율 0~1
THRESHOLD = 0.5     # red_ratio_min
DARK_MAX = 0.1      # dark_ratio_max (흠 비율 상한, 이보다 크면 중)
LOW_MAX = 0.3       # 빨강 비율이 이보다 낮으면 하 (MOCK 전용 기준 — 실제 기준은 로봇 쪽이 정한다)
APPLE_COUNT = 10    # 미션 한 번에 사과 수
BACKGROUNDS = [(40, 44, 52), (30, 50, 40), (50, 36, 36)]


def _grade(v_value: float, dark: float) -> str:
    """상 = 빨강 비율 >= 임계값이고 흠 비율 <= 상한. 하 = 빨강 비율이 LOW_MAX 미만. 나머지 = 중."""
    if v_value >= THRESHOLD and dark <= DARK_MAX:
        return "상"
    return "하" if v_value < LOW_MAX else "중"


def _circles(seed: int) -> list[tuple]:
    rng = random.Random(seed)
    return [
        (160 + 160 * i, 240 + rng.randint(-80, 80), rng.randint(40, 70),
         rng.uniform(0.5, 1.2), rng.uniform(0, math.pi), color)
        for i, color in enumerate([(220, 60, 60), (230, 180, 40), (60, 170, 90)])
    ]


def _positions(circles, t: float):
    return [(int(cx + 40 * math.sin(t * speed + phase)), cy, r, color)
            for cx, cy, r, speed, phase, color in circles]


def _jpeg(background, positions, label: str) -> bytes:
    img = Image.new("RGB", (WIDTH, HEIGHT), background)
    draw = ImageDraw.Draw(img)
    for x, y, r, color in positions:
        draw.ellipse((x - r, y - r, x + r, y + r), fill=color)
    draw.text((10, 10), f"MOCK {label}", fill=(200, 200, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


async def run(cameras: CameraHubs, judges: JudgeHub, stall: bool = False, resume_after: float = 70.0) -> None:
    """stall 이면 첫 미션의 사과 2 를 집은 뒤 resume_after 초 동안 미션 이벤트를 끊었다가 이어 간다
    (서버의 멈춘 미션 처리 재현용, MOCK_STALL=1). 카메라 프레임은 계속 나간다."""
    started = time.time()
    scenes = {
        name: (BACKGROUNDS[i % len(BACKGROUNDS)], _circles(i))
        for i, name in enumerate(cameras.names)
    }
    # 할 일 목록: (시각, 함수). 사과 한 개의 사이클을 실제 미션 순서대로 흉내 낸다.
    schedule: list[tuple[float, object]] = []
    state = {"apple": 0, "scale": 1.0, "release_h": 0.05, "positions": None}

    def mission(event: str, **fields) -> None:
        judges.add_mission({"event": event, "ts": time.time(), **fields})

    def plan_apple(t0: float) -> None:
        """t0 부터 사과 하나: 사과 시작 → pick → (실패면 건너뜀) → inspect → 판정 → place → 모션 → home."""
        state["apple"] += 1
        i = state["apple"]
        if i > APPLE_COUNT:
            schedule.append((t0, lambda: mission("end", duration_s=round(time.time() - state["t_start"], 1),
                                                 results=[])))
            schedule.append((t0 + 3.0, lambda: plan_mission(time.time())))
            return
        picked = random.random() > 0.1
        schedule.append((t0, lambda: (mission("apple", index=i, total=APPLE_COUNT), mission("phase", phase="pick"))))
        schedule.append((t0 + 1.0, lambda: mission("pick", ok=picked, attempt=0,
                                                   width_mm=round(random.uniform(60, 80), 1) if picked else 0.0)))
        if not picked:
            schedule.append((t0 + 1.5, lambda: mission("skip", index=i, reason="파지 실패")))
            schedule.append((t0 + 2.0, lambda: plan_apple(time.time())))
            return
        # MOCK_STALL: 첫 미션의 사과 2 를 집은 뒤 로봇이 멈춘 것처럼 한동안 아무 이벤트도 보내지 않는다
        d = 0.0
        if stall and i == 2 and not state.get("stalled_once"):
            state["stalled_once"] = True
            d = resume_after
        schedule.append((t0 + d + 2.0, lambda: mission("phase", phase="inspect")))
        schedule.append((t0 + d + 3.0, judge))
        schedule.append((t0 + d + 3.0 + MOTION_DELAY, motion))
        schedule.append((t0 + d + 3.5 + MOTION_DELAY, lambda: plan_apple(time.time())))

    def plan_mission(t0: float) -> None:
        state.update(apple=0, t_start=t0)
        mission("start", apple_count=APPLE_COUNT, sim=True)
        plan_apple(t0 + 0.5)

    def judge() -> None:
        x, y, r, _ = random.choice(state["positions"])
        v_value = round(random.uniform(0.2, 0.95), 3)
        dark = round(random.uniform(0.0, 0.15), 3)
        # 로봇 쪽 dashboard.confidence_of 와 같은 식: 임계값에서 떨어진 정도 0.5~1.0
        span = max(THRESHOLD, 1 - THRESHOLD)
        judges.add_judge({
            "grade": _grade(v_value, dark),
            "confidence": round(min(1.0, 0.5 + abs(v_value - THRESHOLD) / (2 * span)), 3),
            "v_value": v_value,
            "threshold": THRESHOLD,
            "bbox": [x - r, y - r, 2 * r, 2 * r],
            "cam": cameras.default,
            "ts": time.time(),
            "extra": {"dark_ratio": dark, "dark_max": DARK_MAX},
        })
        mission("phase", phase="place")

    def motion() -> None:
        rolled = random.random() < 0.2
        judges.add_motion({
            "approach_speed": round(state["scale"], 3),
            "place_height": round(state["release_h"], 4),
            "roll_detected": rolled,
            "ts": time.time(),
        })
        # 굴림이면 20% 감속 (로봇 쪽 adaptive.py 와 같은 규칙), 아니면 조금 복구
        state["scale"] = state["scale"] * 0.8 if rolled else min(1.0, state["scale"] * 1.1)
        state["release_h"] = max(0.02, state["release_h"] * 0.8) if rolled else min(0.05, state["release_h"] * 1.1)
        mission("adaptive", scale=round(state["scale"], 3), release_h=round(state["release_h"], 4),
                frozen=False, down_streak=1 if rolled else 0)
        mission("phase", phase="home")

    plan_mission(started)

    while True:
        now = time.time()
        for name, (background, circles) in scenes.items():
            positions = _positions(circles, now - started)
            if name == cameras.default:
                state["positions"] = positions
            # JPEG 인코딩은 CPU 작업이라 스레드로 넘긴다
            frame = await asyncio.to_thread(_jpeg, background, positions, name)
            cameras.get(name).publish(frame)

        due = [item for item in schedule if item[0] <= now]
        for item in sorted(due, key=lambda it: it[0]):
            schedule.remove(item)
            item[1]()

        await asyncio.sleep(max(0.0, 1.0 / FPS - (time.time() - now)))
