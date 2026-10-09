"""ROS 스레드에서 들어온 카메라 프레임을 asyncio 쪽 웹소켓 클라이언트들에게 넘긴다.

ROS 콜백은 spin 스레드에서 불리므로 call_soon_threadsafe 로 이벤트 루프에 넘긴다.
클라이언트마다 큐를 두지 않고 "가장 최신 프레임" 하나만 들고 있어서,
느린 클라이언트는 중간 프레임을 건너뛰고 항상 최신 화면을 받는다.
"""

import asyncio


class FrameHub:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._event: asyncio.Event | None = None
        self._frame: bytes | None = None
        self._seq = 0

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._event = asyncio.Event()

    def push(self, frame: bytes) -> None:
        """ROS spin 스레드에서 호출된다."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._publish, frame)

    def _publish(self, frame: bytes) -> None:
        self._frame = frame
        self._seq += 1
        event, self._event = self._event, asyncio.Event()
        event.set()

    async def next_frame(self, last_seq: int) -> tuple[int, bytes]:
        """last_seq 이후의 새 프레임이 들어올 때까지 기다린다."""
        while self._seq == last_seq or self._frame is None:
            await self._event.wait()
        return self._seq, self._frame
