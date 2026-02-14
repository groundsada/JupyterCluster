FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Helm 3
RUN curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

# Verify Helm installation
RUN helm version

# Set working directory
WORKDIR /app

# Copy requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY jupytercluster/ /app/jupytercluster/
COPY setup.py pyproject.toml /app/

# Copy templates and static to expected location
# Tornado looks for templates relative to the app module
# Ensure templates are accessible from jupytercluster package
RUN mkdir -p /app/templates /app/static && \
    cp -r /app/jupytercluster/templates/* /app/templates/ && \
    cp -r /app/jupytercluster/static/* /app/static/ || true

# Install application
RUN pip install -e .

# Create data directory
RUN mkdir -p /data

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/health')"

# Run application
CMD ["python", "-m", "jupytercluster"]

