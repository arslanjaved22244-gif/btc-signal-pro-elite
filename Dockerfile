FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements-live.txt .
RUN pip install --no-cache-dir -r requirements-live.txt
COPY . .
EXPOSE 8080
CMD ["python", "live_runner.py"]
