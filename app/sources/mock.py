"""MOCK=1 일 때 로봇과 카메라 대신 프레임과 이벤트를 만드는 개발용 태스크.

카메라마다 단색 배경 위에 원 몇 개를 그린 JPEG 를 만들고,
판정의 bbox 는 기본 카메라 JPEG 에 실제로 그려진 원의 위치와 맞춘다.
판정 / 모션은 /ingest 로 들어오는 것과 같은 형식으로 JudgeHub 에 넣는다.
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
JUDGE_EVERY = 4.0  # 초
MOTION_DELAY = 1.5  # 판정 후 모션 이벤트까지
THRESHOLD = 160
BACKGROUNDS = [(40, 44, 52), (30, 50, 40), (50, 36, 36)]


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


async def run(cameras: CameraHubs, judges: JudgeHub) -> None:
    started = time.time()
    scenes = {
        name: (BACKGROUNDS[i % len(BACKGROUNDS)], _circles(i))
        for i, name in enumerate(cameras.names)
    }
    next_judge = started + JUDGE_EVERY
    pending_motion: float | None = None

    while True:
        now = time.time()
        default_positions = None
        for name, (background, circles) in scenes.items():
            positions = _positions(circles, now - started)
            if name == cameras.default:
                default_positions = positions
            # JPEG 인코딩은 CPU 작업이라 스레드로 넘긴다
            frame = await asyncio.to_thread(_jpeg, background, positions, name)
            cameras.get(name).publish(frame)

        if now >= next_judge:
            next_judge = now + JUDGE_EVERY
            x, y, r, _ = random.choice(default_positions)
            v_value = random.randint(110, 230)
            judges.add_judge({
                "grade": "상" if v_value >= THRESHOLD else "중",
                "confidence": round(random.uniform(0.7, 0.99), 2),
                "v_value": v_value,
                "threshold": THRESHOLD,
                "bbox": [x - r, y - r, 2 * r, 2 * r],
                "cam": cameras.default,
                "ts": now,
            })
            pending_motion = now + MOTION_DELAY

        if pending_motion is not None and now >= pending_motion:
            pending_motion = None
            judges.add_motion({
                "approach_speed": round(random.uniform(0.5, 1.0), 2),
                "place_height": round(random.uniform(0.08, 0.15), 3),
                "roll_detected": random.random() < 0.2,
                "ts": now,
            })

        await asyncio.sleep(max(0.0, 1.0 / FPS - (time.time() - now)))
