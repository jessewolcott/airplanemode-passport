FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Run unprivileged. Passport keeps nothing on disk: rooms and games live in memory.
RUN useradd --system --uid 10001 passport
USER passport
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
