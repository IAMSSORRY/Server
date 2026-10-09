"""PIPER Studio 게이트웨이의 카메라 MJPEG 스트림을 받아 FrameHub 에 넣는다.

PIPER Studio 는 카메라를 camerad 데몬이 독점하고, 게이트웨이가
`GET /api/cameras/{cam_id}/stream` 으로 `multipart/x-mixed-replace` 스트림을 낸다.
카메라마다 연결 하나를 열어 두고, 끊기면 다시 붙는다.
"""

import asyncio
import logging
from urllib.parse import quote

import httpx

from app.stream import FrameHub

log = logging.getLogger(__name__)

RECONNECT_MAX_S = 10.0


async def _read_parts(response: httpx.Response):
    """multipart 응답에서 JPEG 바이트를 하나씩 꺼낸다.

    PIPER 는 파트마다 Content-Length 를 넣어 주므로 그 길이만큼 읽는다.
    """
    buf = b""
    async for chunk in response.aiter_bytes():
        buf += chunk
        while True:
            header_end = buf.find(b"\r\n\r\n")
            if header_end < 0:
                break
            headers = buf[:header_end].decode("latin-1").lower()
            length = None
            for line in headers.split("\r\n"):
                if line.startswith("content-length:"):
                    length = int(line.split(":", 1)[1])
            if length is None:
                # 헤더가 아닌 쓰레기(경계 앞의 빈 줄 등)를 건너뛴다
                buf = buf[header_end + 4:]
                continue
            body_start = header_end + 4
            if len(buf) < body_start + length:
                break
            yield buf[body_start:body_start + length]
            buf = buf[body_start + length:]


async def pull_camera(name: str, cam_id: str, hub: FrameHub, base_url: str, fps: float) -> None:
    url = f"{base_url}/api/cameras/{quote(cam_id, safe='/')}/stream"
    backoff = 1.0
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=30.0)) as client:
        while True:
            try:
                async with client.stream("GET", url, params={"fps": fps}) as response:
                    response.raise_for_status()
                    log.info("PIPER 카메라 연결: %s ← %s", name, url)
                    backoff = 1.0
                    async for jpeg in _read_parts(response):
                        hub.publish(jpeg)
                log.warning("PIPER 카메라 스트림 종료: %s", name)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("PIPER 카메라 %s 연결 실패 (%s), %.0f초 뒤 재시도", name, e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX_S)
