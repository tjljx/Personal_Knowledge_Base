FROM python:3.13-slim-trixie

WORKDIR /app
RUN python -m pip install --no-cache-dir "fastapi>=0.115,<1" "uvicorn>=0.34,<1" "fastembed>=0.7,<1"
COPY apps/reranker/server.py /app/server.py

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
