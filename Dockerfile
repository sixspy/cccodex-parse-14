FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=6200 \
    DATABASE_PATH=/app/instance/parse_lab.db DJANGO_SETTINGS_MODULE=config.settings
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
COPY . .
RUN mkdir -p /app/instance
EXPOSE 6200
CMD ["python", "run.py"]
