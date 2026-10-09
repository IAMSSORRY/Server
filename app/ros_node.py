"""FastAPI 프로세스 안에서 함께 도는 ROS2 노드.

rclpy 의 executor 는 블로킹이라 asyncio 이벤트 루프를 막는다.
그래서 별도 스레드에서 spin 하고, 웹 핸들러는 이 클래스의 메서드만 호출한다.
"""

import os
import threading
from dataclasses import dataclass
from typing import Callable

import rclpy
from rclpy.executors import ExternalShutdownException, SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import String

# image_transport 의 compressed 토픽. 카메라 드라이버에 맞게 환경변수로 바꾼다.
CAMERA_TOPIC = os.environ.get("CAMERA_TOPIC", "/camera/image_raw/compressed")
# 비전 / 모션 노드가 std_msgs/String 에 JSON 을 담아 내는 토픽.
JUDGE_TOPIC = os.environ.get("JUDGE_TOPIC", "/ssorry/judge")
MOTION_TOPIC = os.environ.get("MOTION_TOPIC", "/ssorry/motion")

# 판정 / 모션 이벤트는 빠지면 안 되므로 reliable 로 받는다.
EVENT_QOS = QoSProfile(depth=100, reliability=ReliabilityPolicy.RELIABLE)


@dataclass
class BridgeCallbacks:
    """ROS spin 스레드에서 불리는 콜백. 이벤트 루프로 넘기는 일은 받는 쪽이 한다."""

    on_frame: Callable[[bytes], None] | None = None
    on_judge: Callable[[str], None] | None = None
    on_motion: Callable[[str], None] | None = None


class BridgeNode(Node):
    """웹에서 들어온 요청을 토픽으로 내보내고, 구독한 값을 메모리에 들고 있는 노드."""

    def __init__(self, callbacks: BridgeCallbacks) -> None:
        super().__init__("ssorry_web_bridge")

        self._callbacks = callbacks
        self.create_subscription(
            CompressedImage, CAMERA_TOPIC, self._on_camera, qos_profile_sensor_data
        )
        self.create_subscription(String, JUDGE_TOPIC, self._on_judge, EVENT_QOS)
        self.create_subscription(String, MOTION_TOPIC, self._on_motion, EVENT_QOS)

        self._publisher = self.create_publisher(String, "chatter", 10)
        self.create_subscription(String, "chatter", self._on_chatter, 10)

        self._lock = threading.Lock()
        self._last_message: str | None = None

    def _on_chatter(self, msg: String) -> None:
        with self._lock:
            self._last_message = msg.data
        self.get_logger().info(f"수신: {msg.data}")

    def _on_camera(self, msg: CompressedImage) -> None:
        if self._callbacks.on_frame is not None:
            self._callbacks.on_frame(bytes(msg.data))

    def _on_judge(self, msg: String) -> None:
        if self._callbacks.on_judge is not None:
            self._callbacks.on_judge(msg.data)

    def _on_motion(self, msg: String) -> None:
        if self._callbacks.on_motion is not None:
            self._callbacks.on_motion(msg.data)

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
        self._extra_nodes: list[Node] = []

    def start(
        self,
        callbacks: BridgeCallbacks,
        extra_node_factories: list[Callable[[], Node]] | None = None,
    ) -> None:
        """extra_node_factories 는 같은 executor 에서 함께 spin 할 노드 (예: MOCK 퍼블리셔).

        노드는 rclpy.init() 이후에만 만들 수 있어서 인스턴스 대신 생성 함수를 받는다.
        """
        rclpy.init()

        node = BridgeNode(callbacks)
        executor = SingleThreadedExecutor()
        executor.add_node(node)
        self._extra_nodes = [factory() for factory in (extra_node_factories or [])]
        for extra in self._extra_nodes:
            executor.add_node(extra)

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
        for extra in self._extra_nodes:
            extra.destroy_node()
        if self.node is not None:
            self.node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
