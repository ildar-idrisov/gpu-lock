# Hardened image: multi-stage, pinned base digest, non-root runtime user.
# Rebuild the digest occasionally — `docker pull python:3.12-slim && docker inspect ...`.

FROM python:3.12-slim@sha256:804ddf3251a60bbf9c92e73b7566c40428d54d0e79d3428194edf40da6521286 AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

COPY server/pyproject.toml server/pyproject.toml
COPY server/gpu_lock_server server/gpu_lock_server
COPY LICENSE LICENSE
COPY NOTICE NOTICE
COPY README.md README.md
COPY CHANGELOG.md CHANGELOG.md

RUN pip install --prefix=/install ./server


FROM python:3.12-slim@sha256:804ddf3251a60bbf9c92e73b7566c40428d54d0e79d3428194edf40da6521286

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/install/bin:${PATH}" \
    PYTHONPATH="/install/lib/python3.12/site-packages"

RUN groupadd --system --gid 1000 gpulock \
 && useradd --system --uid 1000 --gid gpulock --home-dir /app --create-home gpulock

WORKDIR /app

COPY --from=build /install /install
COPY LICENSE /app/LICENSE
COPY NOTICE /app/NOTICE

USER gpulock

EXPOSE 8090

HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8090/health').read()"

STOPSIGNAL SIGTERM

CMD ["python", "-m", "gpu_lock_server"]
