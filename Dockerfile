FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8000
ENV UPLOAD_DIR=/app/uploads
ENV MAX_FILE_SIZE_MB=80
ENV MAX_TOTAL_UPLOAD_MB=250
ENV FILE_TTL_SECONDS=86400
ENV CLEANUP_ON_REQUEST=true

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ghostscript \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/uploads

EXPOSE 8000

CMD ["python", "run.py"]
