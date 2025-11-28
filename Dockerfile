FROM python:3.11-slim

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# System dependencies for Playwright
RUN apt-get update && apt-get install -y \
    wget curl ca-certificates build-essential \
    libnss3 libatk-bridge2.0-0 libgtk-3-0 libx11-xcb1 \
    libasound2 libxcomposite1 libxdamage1 libxrandr2 libgbm1 \
    python3-dev gcc && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && playwright install chromium

COPY . .

ENV QUIZ_SECRET="261"
ENV PORT=7860
EXPOSE 7860

CMD ["bash","-lc","uvicorn app:app --host 0.0.0.0 --port ${PORT}"]

