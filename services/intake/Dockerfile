# Shared by every Python service. Build context is the repo root so we can copy
# both the shared library and the service code.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 1) Shared library first — changes rarely, so this layer caches well.
COPY libs/approvalflow_common ./libs/approvalflow_common
RUN pip install --no-cache-dir ./libs/approvalflow_common

# 2) Service-specific dependencies.
COPY services/intake/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 3) Service code.
COPY services/intake/app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
