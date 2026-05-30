FROM python:3.11-slim

# System deps for Pillow / instagrapi
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg-dev zlib1g-dev libpng-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persist sessions across container restarts in production by mounting a volume at /app/sessions.
RUN mkdir -p /app/sessions

ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
