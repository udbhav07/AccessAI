FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PORT=8000

WORKDIR /app

COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

RUN playwright install --with-deps --only-shell chromium

COPY . .

RUN useradd --create-home app && mkdir runs && chown app runs
USER app

EXPOSE 8000

CMD ["gunicorn", "wsgi:app", "--workers", "1", "--threads", "2", "--timeout", "120"]
