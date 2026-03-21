FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir psycopg2-binary requests
COPY agent/app.py .
EXPOSE 9090
CMD ["python", "-u", "app.py"]
