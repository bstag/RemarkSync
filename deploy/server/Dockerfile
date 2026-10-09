FROM python:3.13-slim-bookworm
ARG TARGETARCH
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/
COPY scripts/setup-cloud-client.py scripts/
COPY deploy/server/initialize.py deploy/server/
RUN python scripts/setup-cloud-client.py --arch ${TARGETARCH:-amd64} \
    && python -m pip install --no-cache-dir '.[server]' \
    && mkdir -p /data/library /state/cloud /keys
USER 10001:10001
ENTRYPOINT ["remarksync"]
CMD ["cloud", "watch", "--dest", "/data/library", "--interval", "300"]
