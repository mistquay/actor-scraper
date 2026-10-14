FROM python:3.12-slim
ARG VERSION=dev
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ACTOR_DATA_DIR=/data \
    ACTOR_VERSION=${VERSION}
WORKDIR /app
COPY pyproject.toml /app/pyproject.toml
COPY actor_scraper /app/actor_scraper
RUN pip install --no-cache-dir .
EXPOSE 8891
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8891/health', timeout=3)"
CMD ["actor-scraper-web", "--host", "0.0.0.0", "--port", "8891"]
