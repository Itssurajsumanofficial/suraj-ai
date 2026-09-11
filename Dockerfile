# ===================================================================
#  Suraj AI — Dockerfile
#  Build:   docker build -t suraj-ai .
#  Run:     docker run -d -p 8000:8000 --name suraj suraj-ai
#  (USB phones need --privileged + -v /dev/bus/usb)
# ===================================================================

FROM python:3.11-slim

# Install ADB (platform-tools) — essential for phone control
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        wget unzip ca-certificates libusb-1.0-0 && \
    wget -q https://dl.google.com/android/repository/platform-tools-latest-linux.zip && \
    unzip -q platform-tools-latest-linux.zip -d /opt && \
    rm platform-tools-latest-linux.zip && \
    ln -s /opt/platform-tools/adb /usr/local/bin/adb && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (cached layer)
COPY requirements.txt .
RUN pip3 install --no-cache-dir fastapi "uvicorn[standard]" pillow

# Copy app
COPY . .

ENV HOST=0.0.0.0
ENV PORT=8000

EXPOSE 8000

CMD ["python3", "server.py"]
