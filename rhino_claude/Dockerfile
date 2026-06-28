FROM python:3.12-slim

WORKDIR /app

RUN groupadd --gid 1001 appgroup \
    && useradd --uid 1001 --gid appgroup --no-create-home appuser

COPY requirements.txt .

RUN pip install --upgrade pip --no-cache-dir \
    && pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
