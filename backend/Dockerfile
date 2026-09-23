FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /workspace
COPY pyproject.toml requirements.lock ./
COPY packages ./packages
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps -e .
COPY backend ./backend
COPY agent.py ./
COPY scripts ./scripts
EXPOSE 8000
CMD ["python", "backend/manage.py", "runserver", "0.0.0.0:8000", "--noreload"]
