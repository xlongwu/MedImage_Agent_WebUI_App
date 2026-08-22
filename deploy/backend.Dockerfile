FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV MEDIMAGE_ENV=docker_demo
ENV MEDIMAGE_MATLAB_ENABLED=false
ENV MEDIMAGE_ALLOW_FULL_DPABI_EXECUTION=false
ENV MEDIMAGE_ALLOW_DPARSF_RUN=false
ENV MEDIMAGE_ALLOW_DPARSFA_RUN=false
ENV MEDIMAGE_ALLOW_RAWDATA_WRITE=false
ENV MEDIMAGE_SYNTHETIC_ONLY=true

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY src/backend ./src/backend
COPY specs ./specs
COPY examples ./examples
COPY README.md ./README.md

RUN mkdir -p /app/work /app/reports /app/logs /app/derivatives

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

CMD ["uvicorn", "src.backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
