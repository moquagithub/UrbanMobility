# ============================================================
# Stage 1: Build dependencies
# ============================================================
FROM python:3.12-slim AS builder

WORKDIR /build

# System packages needed to compile Python wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt


# ============================================================
# Stage 2: Runtime image
# ============================================================
FROM python:3.12-slim AS runtime

LABEL maintainer="Mobility PDF Generator"
LABEL description="Automated academic PDF generator for urban mobility data analysis"
LABEL version="1.0"

# Install LaTeX and system tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    texlive-latex-base \
    texlive-latex-extra \
    texlive-latex-recommended \
    texlive-lang-french \
    texlive-fonts-recommended \
    texlive-fonts-extra \
    lmodern \
    latexmk \
    && rm -rf /var/lib/apt/lists/*

# Copy Python packages from builder
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

WORKDIR /app

# Copy project source (respects .dockerignore)
COPY . .

# Create runtime directories
RUN mkdir -p /app/data /app/logs

# Make sure Python can find the project modules
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Default: show help (override with docker run args)
ENTRYPOINT ["python", "orchestrator.py"]
CMD ["--help"]
