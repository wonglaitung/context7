FROM python:3.10-slim

WORKDIR /app

RUN pip install --no-cache-dir mcp[cli] chromadb

COPY mcp_api.py /app/mcp_api.py

EXPOSE 8500

ENTRYPOINT ["python", "/app/mcp_api.py"]