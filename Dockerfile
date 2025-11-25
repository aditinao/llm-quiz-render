FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app
RUN apt-get update && apt-get install -y wget curl ca-certificates build-essential libnss3 libatk-bridge2.0-0 libgtk-3-0 libx11-xcb1 libasound2 libxcomposite1 libxdamage1 libxrandr2 libgbm1 poppler-utils --no-install-recommends && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium
COPY . .
ENV QUIZ_SECRET="261"
ENV REDIS_URL="redis://redis:6379/0"
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
