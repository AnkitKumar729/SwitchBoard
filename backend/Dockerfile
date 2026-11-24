# --- build stage: install the pinned dependencies into a virtualenv ---
FROM python:3.12-slim AS build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- runtime stage: only the virtualenv and the app code, run as a non-root user ---
FROM python:3.12-slim
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LITELLM_LOCAL_MODEL_COST_MAP=True \
    DATABASE_URL=sqlite:////data/app.db
RUN useradd --uid 1000 --create-home app && mkdir /data && chown app:app /data
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY app ./app
USER app
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=4s --start-period=30s --retries=6 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '8000') + '/health', timeout=4)"
# Hosts such as Render pass the port to listen on as $PORT; locally it is 8000.
CMD ["sh", "-c", "exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}"]
