FROM python:3.10-slim

WORKDIR /app

# 先安装 PyTorch CPU 版本，再用 --no-deps 安装 sentence-transformers 防止重装 torch
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir sentence-transformers --no-deps && \
    pip install --no-cache-dir mcp[cli] chromadb transformers huggingface-hub

COPY mcp_api.py /app/mcp_api.py

EXPOSE 8500

ENTRYPOINT ["python", "/app/mcp_api.py"]