"""카메라 프레임을 웹소켓 클라이언트들에게 넘긴다.

프레임은 카메라 소스(PIPER 스트림, /ingest/camera, MOCK)에서 이벤트 루프 안으로 들어온다.
클라이언트마다 큐를 두지 않고 "가장 최신 프레임" 하나만 들고 있어서,
느린 클라이언트는 중간 프레임을 건너뛰고 항상 최신 화면을 받는다.
"""

import asyncio


class FrameHub:
    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._frame: bytes | None = None
        self._seq = 0

    def publish(self, frame: bytes) -> None:
        self._frame = frame
        self._seq += 1
        event, self._event = self._event, asyncio.Event()
        event.set()

    @property
    def has_frame(self) -> bool:
        return self._frame is not None

    async def next_frame(self, last_seq: int) -> tuple[int, bytes]:
        """last_seq 이후의 새 프레임이 들어올 때까지 기다린다."""
        while self._seq == last_seq or self._frame is None:
            await self._event.wait()
        return self._seq, self._frame


class CameraHubs:
    """카메라 이름(예: top, wrist)마다 FrameHub 하나."""

    def __init__(self, names: list[str]) -> None:
        if not names:
            raise ValueError("카메라가 하나 이상 있어야 합니다")
        self._hubs = {name: FrameHub() for name in names}

    @property
    def names(self) -> list[str]:
        return list(self._hubs)

    @property
    def default(self) -> str:
        return next(iter(self._hubs))

    def get(self, name: str | None) -> FrameHub | None:
        return self._hubs.get(name or self.default)
