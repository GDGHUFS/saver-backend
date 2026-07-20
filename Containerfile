FROM docker.io/library/python:3.14-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore \
    WEB_CONCURRENCY=2

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN python -m pip install --requirement requirements.txt gunicorn==26.0.0

COPY src ./src

EXPOSE 5050

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5050/', timeout=2)" || exit 1

CMD ["gunicorn", "src.app:app", "--bind=0.0.0.0:5050", "--worker-class=asgi", "--asgi-lifespan=on", "--access-logfile=-", "--error-logfile=-"]
