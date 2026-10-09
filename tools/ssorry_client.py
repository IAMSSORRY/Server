"""로봇 쪽 프로세스(LeRobot 추론 루프, 비전 판정 스크립트)에서 Ssorry 서버로 보내는 클라이언트.

의존성은 표준 라이브러리 + websockets 뿐이라 LeRobot 가상환경에 그대로 복사해 쓸 수 있다.

    from ssorry_client import SsorryClient

    client = SsorryClient("http://<서버IP>:8000", token=None)
    client.send_judge(grade="상", confidence=0.87, v_value=182, threshold=160,
                      bbox=[412, 188, 96, 96], cam="top")
    client.send_motion(approach_speed=0.8, place_height=0.12, roll_detected=False)

    # 카메라를 LeRobot 이 쥐고 있을 때: 읽은 프레임을 JPEG 로 밀어 넣는다
    pusher = client.camera_pusher("top")
    pusher.push(jpeg_bytes)      # cv2.imencode(".jpg", frame)[1].tobytes()

카메라를 PIPER Studio 가 쥐고 있으면 프레임은 서버가 PIPER 에서 직접 받아오므로
(CAMERA_SOURCE=piper) camera_pusher 는 쓰지 않는다.
"""

import json
import threading
import time
import urllib.request
from typing import Sequence


class SsorryClient:
    def __init__(self, base_url: str, token: str | None = None, timeout: float = 2.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers=self._headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as res:
            return json.loads(res.read())

    def send_judge(
        self,
        grade: str,
        confidence: float,
        v_value: float,
        threshold: float,
        bbox: Sequence[int],
        cam: str | None = None,
        ts: float | None = None,
        cycle_time: float | None = None,
    ) -> dict:
        """판정 하나. bbox 는 그 카메라가 웹으로 내보내는 JPEG 기준 [x, y, w, h] 픽셀."""
        payload = {
            "grade": grade, "confidence": confidence, "v_value": v_value,
            "threshold": threshold, "bbox": list(bbox), "ts": ts or time.time(),
        }
        if cam is not None:
            payload["cam"] = cam
        if cycle_time is not None:
            payload["cycle_time"] = cycle_time
        return self._post("/ingest/judge", payload)

    def send_motion(
        self,
        approach_speed: float,
        place_height: float,
        roll_detected: bool,
        judge_id: int | None = None,
        ts: float | None = None,
    ) -> dict:
        """모션 결과 하나. judge_id 를 주면 그 판정의 roll_detected 로 기록된다."""
        payload = {
            "approach_speed": approach_speed, "place_height": place_height,
            "roll_detected": roll_detected, "ts": ts or time.time(),
        }
        if judge_id is not None:
            payload["id"] = judge_id
        return self._post("/ingest/motion", payload)

    def camera_pusher(self, cam: str) -> "CameraPusher":
        return CameraPusher(self, cam)


class CameraPusher:
    """JPEG 프레임을 백그라운드 스레드로 서버에 보낸다.

    push() 는 막히지 않는다. 보내는 중에 새 프레임이 오면 이전 것을 버리고 최신 것만 보낸다
    (추론 루프를 네트워크가 늦추지 않게).
    """

    def __init__(self, client: SsorryClient, cam: str) -> None:
        url = client.base_url.replace("http://", "ws://").replace("https://", "wss://")
        self._url = f"{url}/ingest/camera/{cam}"
        self._token = client.token
        self._latest: bytes | None = None
        self._cond = threading.Condition()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def push(self, jpeg: bytes) -> None:
        with self._cond:
            self._latest = jpeg
            self._cond.notify()

    def _next(self) -> bytes:
        with self._cond:
            while self._latest is None:
                self._cond.wait()
            frame, self._latest = self._latest, None
            return frame

    def _run(self) -> None:
        from websockets.sync.client import connect

        headers = {"Authorization": f"Bearer {self._token}"} if self._token else None
        while True:
            try:
                with connect(self._url, additional_headers=headers) as ws:
                    while True:
                        ws.send(self._next())
            except Exception as e:  # 서버 재시작 등. 잠깐 쉬고 다시 붙는다
                print(f"[ssorry] camera push 끊김: {e}")
                time.sleep(1.0)
