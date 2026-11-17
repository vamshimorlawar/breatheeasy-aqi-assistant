# Small production image for Render, Cloud Run or any Docker host (uses $PORT, default 7860).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=7860

# Run as an unprivileged user (uid 1000).
RUN useradd --create-home --uid 1000 app
WORKDIR /home/app/code

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=app:app app ./app
COPY --chown=app:app static ./static

USER app
EXPOSE 7860

# --proxy-headers: rate limiting sees the real client IP behind the platform's proxy.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
