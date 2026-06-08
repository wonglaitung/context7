FROM python:3.10-slim

WORKDIR /app

# 一次性安装所有格式解析所需的库（内网环境可指定私有源）
RUN pip install --no-cache-dir sentence-transformers chromadb python-docx pdfplumber

COPY container_sync.py /app/container_sync.py

ENTRYPOINT ["python", "/app/container_sync.py"]