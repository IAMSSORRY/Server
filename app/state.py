"""프로세스 하나에 하나씩만 있는 공유 상태."""

from app import config
from app.db import Store
from app.judge import JudgeHub
from app.sessions import SessionStore
from app.stream import CameraHubs

cameras = CameraHubs(config.CAMERAS)
store = Store(config.DB_PATH)
judges = JudgeHub(default_cam=cameras.default, store=store)
sessions = SessionStore()
