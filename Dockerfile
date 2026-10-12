FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# 安裝 Playwright Chromium 所需的系統依賴與瀏覽器
RUN pip install --no-cache-dir playwright==1.63.0 \
    && playwright install --with-deps chromium

# 安裝專案 Python 套件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製專案程式碼
COPY src/ ./src/
COPY dags/ ./dags/

CMD ["python", "src/run_pipeline.py"]