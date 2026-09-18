# One image for both Python services. The api and the worker run the same
# code against the same database and differ only in their command, so two
# images would be two things to build, scan and keep in step for no gain.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies before source, so editing a file does not reinstall the world.
COPY pyproject.toml README.md* ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY migrations ./migrations
COPY alembic.ini ./

# Non-root. The only thing this process writes is STORAGE_LOCAL_ROOT, which
# in any real deployment is object storage rather than a path in the image.
RUN useradd --create-home --uid 10001 framemeld \
    && mkdir -p /var/framemeld/assets \
    && chown -R framemeld:framemeld /var/framemeld
USER framemeld
ENV STORAGE_LOCAL_ROOT=/var/framemeld/assets

EXPOSE 8000

# --proxy-headers plus FORWARDED_ALLOW_IPS is what makes the login rate
# limiter key on the caller rather than on the proxy; without it every
# request behind an ingress shares one budget. Deliberately uvicorn's job:
# deciding which hop to believe is deployment configuration.
CMD ["sh", "-c", "exec uvicorn framemeld.api_main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-}\""]
