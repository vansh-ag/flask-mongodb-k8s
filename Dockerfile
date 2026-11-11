# Small official Python 3.11 base image (matches the local environment)
FROM python:3.11-slim

# Copy the uv binaries from Astral's official image (pinned version)
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /bin/

# Don't write .pyc files and show logs immediately
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first so Docker can cache this layer
COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

# Copy the application code
COPY app.py .

# Run as a non-root user for better security
RUN useradd --create-home appuser
USER appuser

EXPOSE 5000

CMD ["python", "app.py"]
