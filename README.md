# Ssorry — ROS2 + FastAPI 웹 서버

macOS(Apple Silicon)에는 rclpy 를 네이티브로 설치할 수 없어서, ROS2 Humble 컨테이너
(`ros:humble-ros-base`, Ubuntu 22.04 / Python 3.10) 안에서 FastAPI 를 돌린다.
PyCharm 은 이 컨테이너를 원격 인터프리터로 붙여서 rclpy 자동완성과 디버깅을 쓴다.

## 구조

```
Dockerfile           ros:humble + fastapi/uvicorn
docker-compose.yml   포트 8000, app/ 를 마운트해 --reload
requirements.txt     fastapi, uvicorn, pydantic
app/main.py          FastAPI 엔드포인트
app/ros_node.py      rclpy 노드 + spin 스레드 관리
```

rclpy 의 executor 는 블로킹이라 asyncio 루프를 막는다. 그래서 `RosBridge` 가
별도 스레드에서 spin 하고, FastAPI 핸들러는 노드 객체의 메서드만 호출한다.
노드의 생명주기는 FastAPI lifespan 에 묶여 있다.

## 실행

공유 네트워크는 최초 한 번만 만들면 된다.

```bash
docker network create ssorry-rosnet   # 최초 1회
docker compose up -d
docker compose logs -f api
```

| 엔드포인트 | 설명 |
|---|---|
| `GET /health` | 노드 기동 확인 |
| `POST /publish` | `{"text": "..."}` 를 `/chatter` 토픽으로 발행 |
| `GET /last` | `/chatter` 에서 마지막으로 수신한 값 |
| `GET /topics` | 현재 보이는 토픽 목록 |
| `GET /docs` | Swagger UI |

컨테이너 안에서 ROS2 CLI 를 쓰려면:

```bash
docker compose exec api bash
ros2 topic echo /chatter
```

## PyCharm 인터프리터 설정

`Settings → Project: Ssorry → Python Interpreter → Add Interpreter → On Docker Compose`

- Configuration file: `docker-compose.yml`
- Service: `api`
- Python interpreter path: `/usr/bin/python3`

이렇게 잡으면 `/opt/ros/humble/lib/python3.10/site-packages` 가 인터프리터 경로에
자동으로 포함되어 `rclpy` import 에 빨간 줄이 사라진다.
Content Root 를 따로 추가할 필요는 없다.

> 로컬 venv 나 Homebrew Python(3.14) 은 쓰지 않는다. rclpy 가 없다.

## 다른 ROS2 노드 붙이기

macOS 의 Docker Desktop 은 `network_mode: host` 를 지원하지 않는다. 대신
`ssorry-rosnet` 이라는 외부 공유 네트워크를 쓰므로, 다른 ROS2 컨테이너를
같은 네트워크에 띄우면 DDS 디스커버리가 서로를 찾는다.

```bash
docker run --rm -it \
  --network ssorry-rosnet \
  -e ROS_DOMAIN_ID=0 \
  -e RMW_IMPLEMENTATION=rmw_fastrtps_cpp \
  ros:humble-ros-base \
  ros2 topic echo /chatter
```

`ROS_DOMAIN_ID` 와 `RMW_IMPLEMENTATION` 은 `api` 서비스와 반드시 같아야 한다.
다른 compose 프로젝트에서 붙일 때도 네트워크를 `external: true` / `name: ssorry-rosnet`
으로 선언하면 된다.
