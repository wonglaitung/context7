FROM python:3.10-slim

WORKDIR /app

# 安装 PyTorch CPU 版本和兼容的 transformers 版本
# transformers>=4.41 需要 PyTorch>=2.2 支持 float8 类型
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir sentence-transformers --no-deps && \
    pip install --no-cache-dir chromadb python-docx pdfplumber scikit-learn && \
    pip install --no-cache-dir "transformers>=4.41,<4.50" huggingface-hub

COPY container_sync.py /app/container_sync.py

ENTRYPOINT ["python", "/app/container_sync.py"]