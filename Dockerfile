FROM python:3.12-slim

WORKDIR /app

# git needed for PR creation; keep image lean otherwise
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# default: one simulate-mode detection cycle with dry-run notifications
ENTRYPOINT ["python", "main.py"]
CMD ["--mode", "simulate", "--once", "--dry-run"]
