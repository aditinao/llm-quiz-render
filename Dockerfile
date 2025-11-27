FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# system deps for Playwright
RUN apt-get update && apt-get install -y \
    wget curl ca-certificates build-essential \
    libnss3 libatk-bridge2.0-0 libgtk-3-0 \
    libx11-xcb1 libasound2 libxcomposite1 \
    libxdamage1 libxrandr2 libgbm1 \
    python3-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# install Playwright browsers (Chromium)
RUN playwright install chromium

COPY . .

# default secret for local dev; on HF you can override via Space secrets if you want
ENV QUIZ_SECRET="261"

# Hugging Face expects port 7860
EXPOSE 7860

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860"]
