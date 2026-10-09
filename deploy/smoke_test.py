"""CI 스모크 테스트. MOCK 서버를 띄워 주요 경로가 응답하는지만 본다.

    python deploy/smoke_test.py
"""

import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request

import websockets

PORT = 8765
BASE = f"localhost:{PORT}"


def get(path: str, cookie: str | None = None):
    req = urllib.request.Request(f"http://{BASE}{path}", headers={"Cookie": cookie} if cookie else {})
    return urllib.request.urlopen(req, timeout=5)


def wait_ready(proc: subprocess.Popen) -> None:
    for _ in range(50):
        if proc.poll() is not None:
            sys.exit("서버가 시작하자마자 죽었다")
        try:
            get("/health")
            return
        except OSError:
            time.sleep(0.2)
    sys.exit("서버가 10초 안에 뜨지 않았다")


async def check(cookie: str) -> None:
    headers = {"Cookie": cookie}
    async with websockets.connect(f"ws://{BASE}/ws/camera?cam=top", additional_headers=headers) as ws:
        hello = json.loads(await ws.recv())
        assert hello["type"] == "hello" and hello["cam"] == "top", hello
        frame = await asyncio.wait_for(ws.recv(), 5)
        assert isinstance(frame, bytes) and frame[:2] == b"\xff\xd8", "JPEG 프레임이 아니다"

    async with websockets.connect(f"ws://{BASE}/ws/judge", additional_headers=headers) as ws:
        snapshot = json.loads(await ws.recv())
        assert snapshot["type"] == "snapshot", snapshot

    async with websockets.connect(f"ws://{BASE}/ws/judge") as ws:
        try:
            await ws.recv()
        except websockets.ConnectionClosed:
            pass
        assert ws.close_code == 4401, f"쿠키 없는 연결이 4401 로 닫히지 않았다: {ws.close_code}"


def main() -> None:
    env = {**os.environ, "MOCK": "1", "FRONTEND_DIR": "/nonexistent"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT)],
        env=env,
    )
    try:
        wait_ready(proc)
        res = get("/session")
        cookie = res.headers["set-cookie"].split(";")[0]
        cams = json.loads(get("/cameras").read())
        assert [c["name"] for c in cams["cameras"]] == ["top", "wrist"], cams
        stats = json.loads(get("/stats?limit=5").read())
        assert set(stats) == {"stats", "cycle_time", "recent"}, stats
        assert get("/").status == 200
        asyncio.run(check(cookie))
        print("스모크 테스트 통과")
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    main()
