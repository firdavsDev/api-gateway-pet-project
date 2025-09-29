FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY locustfile.py .

# CMD ["locust", "-H", "http://gateway:8080", "--headless", "-u", "100", "-r", "20", "--run-time", "30s"]
CMD ["locust", "-f", "locustfile.py", "--host", "http://gateway:8080"]
