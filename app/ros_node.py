"""FastAPI 프로세스 안에서 함께 도는 ROS2 노드.

rclpy 의 executor 는 블로킹이라 asyncio 이벤트 루프를 막는다.
그래서 별도 스레드에서 spin 하고, 웹 핸들러는 이 클래스의 메서드만 호출한다.
"""

import threading

import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import String


class BridgeNode(Node):
    """웹에서 들어온 요청을 토픽으로 내보내고, 구독한 값을 메모리에 들고 있는 노드."""

    def __init__(self) -> None:
        super().__init__("ssorry_web_bridge")

        self._publisher = self.create_publisher(String, "chatter", 10)
        self.create_subscription(String, "chatter", self._on_chatter, 10)

        self._lock = threading.Lock()
        self._last_message: str | None = None

    def _on_chatter(self, msg: String) -> None:
        with self._lock:
            self._last_message = msg.data
        self.get_logger().info(f"수신: {msg.data}")

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

    def start(self) -> None:
        rclpy.init()
        self.node = BridgeNode()
        self._executor = SingleThreadedExecutor()
        self._executor.add_node(self.node)

        self._thread = threading.Thread(target=self._executor.spin, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._executor is not None:
            self._executor.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self.node is not None:
            self.node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
