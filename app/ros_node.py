"""FastAPI 프로세스 안에서 함께 도는 ROS2 노드.

rclpy 의 executor 는 블로킹이라 asyncio 이벤트 루프를 막는다.
그래서 별도 스레드에서 spin 하고, 웹 핸들러는 이 클래스의 메서드만 호출한다.
"""

import os
import threading
from typing import Callable

import rclpy
from rclpy.executors import ExternalShutdownException, SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import String

# image_transport 의 compressed 토픽. 카메라 드라이버에 맞게 환경변수로 바꾼다.
CAMERA_TOPIC = os.environ.get("CAMERA_TOPIC", "/camera/image_raw/compressed")


class BridgeNode(Node):
    """웹에서 들어온 요청을 토픽으로 내보내고, 구독한 값을 메모리에 들고 있는 노드."""

    def __init__(self, on_frame: Callable[[bytes], None] | None = None) -> None:
        super().__init__("ssorry_web_bridge")

        self._on_frame = on_frame
        self.create_subscription(
            CompressedImage, CAMERA_TOPIC, self._on_camera, qos_profile_sensor_data
        )

        self._publisher = self.create_publisher(String, "chatter", 10)
        self.create_subscription(String, "chatter", self._on_chatter, 10)

        self._lock = threading.Lock()
        self._last_message: str | None = None

    def _on_chatter(self, msg: String) -> None:
        with self._lock:
            self._last_message = msg.data
        self.get_logger().info(f"수신: {msg.data}")

    def _on_camera(self, msg: CompressedImage) -> None:
        if self._on_frame is not None:
            self._on_frame(bytes(msg.data))

    def publish(self, text: str) -> None:
        self._publisher.publish(String(data=text))

    @property
    def last_message(self) -> str | None:
        with self._lock:
            return self._last_message


class RosBridge:
    """rclpy 초기화 / spin 스레드 / 종료를 한 곳에서 관리한다."""

    def __init__(self) -> None:
        self.node: BridgeNode | None = None
        self._executor: SingleThreadedExecutor | None = None
        self._thread: threading.Thread | None = None

    def start(self, on_frame: Callable[[bytes], None] | None = None) -> None:
        rclpy.init()

        node = BridgeNode(on_frame)
        executor = SingleThreadedExecutor()
        executor.add_node(node)

        thread = threading.Thread(target=self._spin, args=(executor,), daemon=True)
        thread.start()

        self.node, self._executor, self._thread = node, executor, thread

    @staticmethod
    def _spin(executor: SingleThreadedExecutor) -> None:
        try:
            executor.spin()
        except ExternalShutdownException:
            # stop() 에서 rclpy.shutdown() 을 부르면 정상적으로 여기로 빠져나온다.
            pass

    def stop(self) -> None:
        if self._executor is not None:
            self._executor.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self.node is not None:
            self.node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
