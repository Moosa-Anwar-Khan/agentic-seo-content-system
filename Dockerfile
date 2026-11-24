FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LITELLM_LOCAL_MODEL_COST_MAP=True XDG_CACHE_HOME=/tmp/cache HF_HOME=/tmp/huggingface
WORKDIR /app
COPY . /app
RUN python -m pip install --no-cache-dir -r requirements-lock.txt     && groupadd --gid 10001 seo     && useradd --uid 10001 --gid seo --no-create-home seo     && mkdir -p /data /outputs     && chown -R seo:seo /data /outputs
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
