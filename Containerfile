FROM docker.io/library/python:3.14-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN python -m pip install --requirement requirements.txt

COPY src ./src

EXPOSE 5050

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5050/', timeout=2)" || exit 1

# TODO: 운영 동시성 기준을 정한 뒤 gunicorn의
# uvicorn.workers.UvicornWorker를 사용하는 다중 worker 구성으로 전환한다.
CMD ["uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "5050"]
