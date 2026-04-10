# Python ka official image use karein
FROM python:3.10-slim

# System updates aur libmagic install karein
RUN apt-get update && apt-get install -y \
    libmagic1 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Work directory set karein
WORKDIR /app

# Requirements copy aur install karein
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Baaki saara code copy karein
COPY . .

# Bot start karein
CMD ["python", "wp.py"]

