from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.ros_node import RosBridge

bridge = RosBridge()


@asynccontextmanager
async def lifespan(app: FastAPI):
    bridge.start()
    yield
    bridge.stop()


app = FastAPI(title="Ssorry ROS2 Web API", lifespan=lifespan)


class PublishRequest(BaseModel):
    text: str


@app.get("/health")
async def health():
    node = bridge.node
    return {
        "status": "ok",
        "ros_node": node.get_name() if node else None,
    }


@app.post("/publish")
async def publish(req: PublishRequest):
    if bridge.node is None:
        raise HTTPException(status_code=503, detail="ROS 노드가 아직 준비되지 않았습니다")
    bridge.node.publish(req.text)
    return {"published": req.text}


@app.get("/last")
async def last():
    if bridge.node is None:
        raise HTTPException(status_code=503, detail="ROS 노드가 아직 준비되지 않았습니다")
    return {"last_message": bridge.node.last_message}


@app.get("/topics")
async def topics():
    if bridge.node is None:
        raise HTTPException(status_code=503, detail="ROS 노드가 아직 준비되지 않았습니다")
    return {
        name: types
        for name, types in bridge.node.get_topic_names_and_types()
    }
