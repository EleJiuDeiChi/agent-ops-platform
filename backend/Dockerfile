FROM python:3.12-alpine3.23

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN apk upgrade --no-cache \
    && addgroup --system --gid 10001 aiops \
    && adduser --system --uid 10001 --ingroup aiops --home /home/aiops \
        --shell /sbin/nologin aiops

WORKDIR /app

COPY backend/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --disable-pip-version-check -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt

COPY --chown=aiops:aiops backend/app /app/app
RUN mkdir -p /app/data && chown -R aiops:aiops /app/data

USER 10001:10001

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-server-header"]
