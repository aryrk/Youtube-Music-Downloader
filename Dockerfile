# ─── Stage 1: Build React frontend ──────────────────────────────────────────
FROM node:20-slim AS frontend-builder

WORKDIR /build

# Install dependencies first (cached layer)
COPY frontend/package.json ./
RUN npm install

# Copy source and build
COPY frontend/ ./
RUN npm run build

# ─── Stage 2: Python runtime ─────────────────────────────────────────────────
FROM python:3.11-slim

# System dependencies
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        xvfb \
        x11vnc \
        websockify \
        wget \
        gnupg \
        unzip \
        && \
    curl -fsSL https://deno.land/x/install/install.sh | sh && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

ENV PATH="/root/.deno/bin:${PATH}"

WORKDIR /app

# Python dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright chromium (for optional Google auth flow)
RUN playwright install chromium --with-deps 2>/dev/null || true

# Download noVNC
RUN mkdir -p /tmp/novnc-src /app/novnc && \
    (curl -fsSL https://github.com/novnc/noVNC/archive/refs/tags/v1.4.0.tar.gz | \
     tar -xz --strip-components=1 -C /tmp/novnc-src && \
     cp -r /tmp/novnc-src/. /app/novnc/) || true

# Copy backend source
COPY backend/ .

# Copy built frontend
COPY --from=frontend-builder /build/dist ./static

# Data directories
RUN mkdir -p /app/downloads /app/data /app/temp_cookies

EXPOSE 8000

COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh
CMD ["/app/start.sh"]
