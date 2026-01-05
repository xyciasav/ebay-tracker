FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# System deps (optional but helpful)
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

# Run as non-root (optional)
RUN useradd -m appuser
USER appuser

EXPOSE 5055

# Production server
CMD ["gunicorn", "-b", "0.0.0.0:5055", "app:create_app()", "--workers", "2", "--threads", "4", "--timeout", "120"]
