FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY libs/approvalflow_common ./libs/approvalflow_common
RUN pip install --no-cache-dir ./libs/approvalflow_common

COPY services/mcp-server/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY services/mcp-server/app ./app
COPY services/mcp-server/data ./data

EXPOSE 8000
CMD ["python", "-m", "app.main"]
