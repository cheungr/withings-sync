FROM python:3.12-slim

ARG PUID=1000
ARG PGID=1000

RUN apt-get update && apt-get install -y --no-install-recommends tzdata && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd --gid "${PGID}" app && \
    useradd --uid "${PUID}" --gid app --create-home --shell /usr/sbin/nologin app && \
    mkdir -p /app /data && chown app:app /app /data

WORKDIR /app
COPY pyproject.toml README.md ./
COPY withings_sync ./withings_sync
RUN pip install --no-cache-dir .

ENV DATA_DIR=/data \
    PYTHONUNBUFFERED=1

USER app
ENTRYPOINT ["withings-sync"]
CMD []
