FROM ros:humble-ros-base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ROS_DOMAIN_ID=0

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-pip \
        ros-humble-example-interfaces \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY app ./app

# ROS2 환경을 먼저 source 한 뒤 CMD 를 실행한다.
ENTRYPOINT ["/ros_entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
