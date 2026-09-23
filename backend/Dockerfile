FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /workspace
COPY pyproject.toml requirements.lock ./
COPY packages ./packages
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps -e .
COPY backend ./backend
COPY agent.py ./
COPY scripts ./scripts
COPY data/demo ./data/demo
RUN python backend/manage.py collectstatic --noinput && \
    useradd --uid 10001 --create-home redorda && \
    mkdir -p artifacts data/participant-kit backend/media && chown -R redorda:redorda /workspace
USER redorda
EXPOSE 8000
CMD ["gunicorn", "--chdir", "backend", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "60"]
