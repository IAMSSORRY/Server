"""PIPER Studio 게이트웨이의 카메라 MJPEG 스트림을 받아 FrameHub 에 넣는다.

PIPER Studio 는 카메라를 camerad 데몬이 독점하고, 게이트웨이가
`GET /api/cameras/{cam_id}/stream` 으로 `multipart/x-mixed-replace` 스트림을 낸다.
카메라마다 연결 하나를 열어 두고, 끊기면 다시 붙는다.

PIPER 의 일반 USB 카메라 id 는 `/dev/videoN` 이라 재부팅이나 USB 재연결로 번호가 바뀐다.
그래서 설정에는 PIPER 화면에서 붙인 **라벨**을 적고, 연결할 때마다
`GET /api/cameras/current` 에서 그 라벨의 현재 id 를 찾는다.
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


async def resolve_camera_id(client: httpx.AsyncClient, base_url: str, ref: str) -> str:
    """라벨(또는 id, profile_key, 표시명) → PIPER 의 현재 카메라 id.

    목록에서 못 찾으면 ref 를 id 로 보고 그대로 쓴다.
    """
    response = await client.get(f"{base_url}/api/cameras/current")
    response.raise_for_status()
    cams = response.json().get("cameras", [])
    for key in ("label", "id", "profile_key", "display_name"):
        matches = [c for c in cams if c.get(key) == ref]
        if len(matches) == 1:
            cam = matches[0]
            if cam.get("connected") is False:
                # 연결 안 된 카메라의 스트림은 멈춘 프레임만 준다. 붙지 말고 다시 확인한다.
                raise RuntimeError(f"PIPER 에서 {ref!r}({cam['id']}) 가 연결돼 있지 않다 — 카메라 페이지에서 연결하세요")
            return cam["id"]
        if len(matches) > 1:
            raise RuntimeError(f"PIPER 카메라 {key}={ref!r} 가 {len(matches)}개라 고를 수 없다")
    known = ", ".join(f"{c.get('label') or '-'}({c.get('id')})" for c in cams) or "없음"
    log.warning("PIPER 카메라 목록에 %r 가 없어 id 로 보고 그대로 쓴다. 등록된 카메라: %s", ref, known)
    return ref


async def pull_camera(name: str, ref: str, hub: FrameHub, base_url: str, fps: float) -> None:
    """ref 는 PIPER 카메라 라벨(권장) 또는 id."""
    backoff = 1.0
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=30.0)) as client:
        while True:
            try:
                # 재연결할 때마다 다시 찾는다 — 그 사이 장치 번호가 바뀌었을 수 있다
                cam_id = await resolve_camera_id(client, base_url, ref)
                url = f"{base_url}/api/cameras/{quote(cam_id, safe='/')}/stream"
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
