"""MOCK=1 일 때 로봇과 카메라 대신 이벤트를 발행하는 개발용 노드.

실제 비전 / 모션 노드와 같은 토픽, 같은 JSON 형식으로 낸다.
카메라 프레임은 단색 배경 위에 원 몇 개를 그린 JPEG 이고,
판정의 bbox 는 그 JPEG 에 실제로 그려진 원의 위치와 맞춘다.
"""

import io
import json
import math
import random
import time

from PIL import Image, ImageDraw
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import String

from app.ros_node import CAMERA_TOPIC, EVENT_QOS, JUDGE_TOPIC, MOTION_TOPIC

WIDTH, HEIGHT = 640, 480
FPS = 10
JUDGE_EVERY = 4.0  # 초
MOTION_DELAY = 1.5  # 판정 후 모션 이벤트까지
THRESHOLD = 160


class MockPublisher(Node):
    def __init__(self) -> None:
        super().__init__("ssorry_mock")

        self._camera = self.create_publisher(CompressedImage, CAMERA_TOPIC, qos_profile_sensor_data)
        self._judge = self.create_publisher(String, JUDGE_TOPIC, EVENT_QOS)
        self._motion = self.create_publisher(String, MOTION_TOPIC, EVENT_QOS)

        self._started = time.time()
        self._next_judge = self._started + JUDGE_EVERY
        self._pending_motion: float | None = None
        # (cx, cy, r, 속도, 위상, 색) — 원마다 좌우로 천천히 흔들린다.
        self._circles = [
            (160 + 160 * i, 240 + random.randint(-80, 80), random.randint(40, 70),
             random.uniform(0.5, 1.2), random.uniform(0, math.pi), color)
            for i, color in enumerate([(220, 60, 60), (230, 180, 40), (60, 170, 90)])
        ]

        self.create_timer(1.0 / FPS, self._tick)
        self.get_logger().info(f"MOCK 퍼블리셔 시작: {CAMERA_TOPIC}, {JUDGE_TOPIC}, {MOTION_TOPIC}")

    def _circle_positions(self, now: float) -> list[tuple[int, int, int, tuple[int, int, int]]]:
        t = now - self._started
        return [
            (int(cx + 40 * math.sin(t * speed + phase)), cy, r, color)
            for cx, cy, r, speed, phase, color in self._circles
        ]

    def _tick(self) -> None:
        now = time.time()
        circles = self._circle_positions(now)
        self._publish_frame(circles)

        if now >= self._next_judge:
            self._next_judge = now + JUDGE_EVERY
            self._publish_judge(now, circles)
            self._pending_motion = now + MOTION_DELAY

        if self._pending_motion is not None and now >= self._pending_motion:
            self._pending_motion = None
            self._publish_motion(now)

    def _publish_frame(self, circles) -> None:
        img = Image.new("RGB", (WIDTH, HEIGHT), (40, 44, 52))
        draw = ImageDraw.Draw(img)
        for x, y, r, color in circles:
            draw.ellipse((x - r, y - r, x + r, y + r), fill=color)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        msg = CompressedImage(format="jpeg")
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.data = buf.getvalue()
        self._camera.publish(msg)

    def _publish_judge(self, now: float, circles) -> None:
        x, y, r, _ = random.choice(circles)
        v_value = random.randint(110, 230)
        grade = "상" if v_value >= THRESHOLD else "중"
        payload = {
            "grade": grade,
            "confidence": round(random.uniform(0.7, 0.99), 2),
            "v_value": v_value,
            "threshold": THRESHOLD,
            "bbox": [x - r, y - r, 2 * r, 2 * r],
            "ts": now,
        }
        self._judge.publish(String(data=json.dumps(payload, ensure_ascii=False)))

    def _publish_motion(self, now: float) -> None:
        payload = {
            "approach_speed": round(random.uniform(0.5, 1.0), 2),
            "place_height": round(random.uniform(0.08, 0.15), 3),
            "roll_detected": random.random() < 0.2,
            "ts": now,
        }
        self._motion.publish(String(data=json.dumps(payload)))
