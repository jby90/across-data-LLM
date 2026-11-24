ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.5
FROM ${UV_IMAGE} AS uvtool
FROM python:3.12-slim-bookworm AS native

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

# uv:Apache-2.0 / MIT
# Unmodified dynamic Pango/GLib/HarfBuzz libraries; Debian notices stay in image.
ARG APT_MIRROR=https://deb.debian.org/debian
ARG APT_SECURITY_MIRROR=https://deb.debian.org/debian-security
RUN sed -i "s|http://deb.debian.org/debian-security|${APT_SECURITY_MIRROR}|g; s|http://deb.debian.org/debian|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
    fonts-liberation fonts-dejavu-core libreoffice-writer-nogui \
    tesseract-ocr tesseract-ocr-eng tesseract-ocr-ara tesseract-ocr-fra \
    && rm -rf /var/lib/apt/lists/*

FROM native AS dependencies
COPY --from=uvtool /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY wheelhouse /opt/wheelhouse
ARG UV_OFFLINE
# 只安装生产依赖(不含 dev 组,因此不含 pgserver 等测试用包);默认在线按锁文件安装,仅显式传入 UV_OFFLINE=1 时走离线 wheelhouse
RUN if [ "${UV_OFFLINE:-0}" = "1" ] || [ "${UV_OFFLINE:-false}" = "true" ]; then \
      test -f /opt/wheelhouse/requirements-linux.txt || { echo "Offline build requested (UV_OFFLINE=1), but backend/wheelhouse/requirements-linux.txt is missing." >&2; exit 1; }; \
      uv venv .venv --python /usr/local/bin/python3 && \
      uv pip install --python .venv/bin/python --require-hashes --no-index \
        --find-links /opt/wheelhouse -r /opt/wheelhouse/requirements-linux.txt; \
    else uv sync --frozen --no-dev --no-install-project; fi
# Pyphen's code is tri-licensed; this distribution elects MPL-1.1. Its unused
# dictionaries have separate licenses and are deliberately not distributed.
RUN .venv/bin/python -c "import pathlib,pyphen,shutil; p=pathlib.Path(pyphen.__file__).parent/'dictionaries'; shutil.rmtree(p); p.mkdir()"
# 从随包已校验 OFL-1.1 可变字体生成静态 Regular(400) 与 Bold(700) 供容器内 LibreOffice 渲染多语种公文与官方表格,不改动原包文件哈希
COPY app/analysis/fonts /tmp/cysh-fontbuild/fonts
RUN .venv/bin/python /tmp/cysh-fontbuild/fonts/build_static_faces.py /tmp/cysh-fontbuild/fonts /opt/cyshuhe-fonts

FROM native AS base
WORKDIR /app
COPY --from=dependencies /app/.venv /app/.venv
COPY --from=dependencies /opt/cyshuhe-fonts /usr/local/share/fonts/cyshuhe
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./

RUN useradd --system --uid 10001 appuser \
    && mkdir -p /data/archive /tmp/cyshuhe-cache/fontconfig \
    && chown -R appuser /data /tmp/cyshuhe-cache
ENV CYSH_ARCHIVE_DIR=/data/archive \
    FONTCONFIG_FILE=/app/app/analysis/fonts/fontconfig.xml \
    XDG_CACHE_HOME=/tmp/cyshuhe-cache \
    PATH=/app/.venv/bin:$PATH
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
