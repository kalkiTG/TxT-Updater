# Stable, small image that works well on Render
FROM python:3.10-slim

# Prevent Python from writing .pyc files and buffering stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# System deps (slim image already sufficient for these libs)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirement list and install
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy the rest of the code
COPY . /app

# Render expects a web service to bind to PORT
EXPOSE 10000

# Run the combined Flask + Telethon app
CMD ["python", "app.py"]
