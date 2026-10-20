FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DCSJ_DATA_DIR=/app/.runtime
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home --uid 10001 dcsj
COPY dcsj/*.py ./dcsj/
COPY frontend/ ./frontend/
COPY data/schema.sql data/README.md ./data/
COPY run.py ./
RUN mkdir -p /app/.runtime /app/dcsj/generated && chown -R dcsj:dcsj /app/.runtime
USER dcsj
EXPOSE 8767
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8767/api/v1/status',timeout=3)"
CMD ["python", "run.py", "--host", "0.0.0.0"]
