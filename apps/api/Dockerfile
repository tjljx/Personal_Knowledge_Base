FROM postgres:16-bookworm AS pg-client

FROM python:3.13-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/apps/api \
    PATH=/usr/lib/postgresql/16/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client poppler-utils tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng libreoffice-writer-nogui fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY --from=pg-client /usr/lib/postgresql/16/bin/pg_dump /usr/lib/postgresql/16/bin/pg_dump
COPY --from=pg-client /usr/lib/postgresql/16/bin/pg_restore /usr/lib/postgresql/16/bin/pg_restore

WORKDIR /app
COPY apps/api /app/apps/api
RUN python -m pip install --no-cache-dir -e /app/apps/api

RUN useradd --system --uid 10001 --home /app --shell /usr/sbin/nologin knowledge \
    && mkdir -p /app/data/files \
    && chown -R knowledge:knowledge /app/data
USER knowledge

CMD ["python", "-m", "uvicorn", "knowledge_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
