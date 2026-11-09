FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY qecho ./qecho
RUN pip install --no-cache-dir . && useradd --create-home --uid 10001 qecho \
    && mkdir -p /app/.data && chown qecho:qecho /app/.data
USER qecho
ENV QECHO_HOST=0.0.0.0 QECHO_PORT=8765 QECHO_DATA_DIR=/app/.data
EXPOSE 8765
VOLUME ["/app/.data"]
CMD ["qecho"]
