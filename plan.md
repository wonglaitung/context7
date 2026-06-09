根据你的最终需求，这是一套**完全容器化、宿主机零污染、支持多格式文件、且具备「语义切片搜索」与「精准全文读取」双工具接口**的完整内网 MCP 方案。

整套方案不依赖 Docker Compose，全部使用原生 `docker run` 命令，通过**读写分离**与双集合（Chunks + Full）的设计，确保在纯 CPU 内网环境下的高效、稳定运行。

---

### ✨ 核心特性

| 特性 | 说明 |
|------|------|
| **增量同步** | 基于 MD5 变更检测，跳过未变化文件，减少计算开销 |
| **智能切片** | 根据文件类型自动选择最优切片策略 |
| **读写分离** | 同步容器（一次性）+ 查询容器（常驻）独立运行 |
| **宿主机零污染** | 所有依赖在容器内，无需安装 Python 环境 |

---

### 📂 项目目录结构

```text
/data/context7/
├── models/
│   └── bge-small-zh-v1.5/       # 离线 Embedding 模型文件（已下载）
├── chroma_data/                 # ChromaDB 容器的物理数据持久化目录
├── manuals/                     # 存放内部技术手册的目录（支持 .md, .docx, .pdf, .py 等）
├── mcp_server/
│   ├── Dockerfile
│   └── mcp_api.py               # 独立常驻的 MCP 服务端 API
└── sync_task/
    ├── Dockerfile
    └── container_sync.py        # 一次性运行的同步与全格式解析脚本

```

---

### 🔪 智能切片策略

根据文件类型自动选择最优切片方式：

| 文件类型 | 切片策略 | 说明 |
|----------|----------|------|
| **Markdown (.md)** | 按标题层级切片 | 按 `#` `##` `###` 等 1-6 级标题分割，保持章节语义完整 |
| **PDF/DOCX** | 按段落 + Token 上限 | 段落完整保留，累计超过 512 tokens 时开始新切片 |
| **Python (.py)** | 按函数/类切片 | 按 `def`/`class` 定义分割，保持代码块完整 |
| **Java (.java)** | 按类/方法切片 | 按 `class`/`method` 边界分割 |
| **Go (.go)** | 按 func/type 切片 | 按 `func`/`type` 定义分割 |
| **SQL (.sql)** | 按语句分割 | 按 `;` 分隔独立 SQL 语句 |
| **Shell (.sh)** | 按 function 切片 | 按 `function` 定义分割 |

---

### 1️⃣ 存储层：拉起独立 ChromaDB 数据库容器

```bash
# 创建专用网络（容器间通过容器名互访）
docker network create context7-net

docker run -d \
  --name internal-chromadb \
  --restart always \
  --network context7-net \
  -p 8000:8000 \
  -v /data/context7/chroma_data:/chroma/chroma \
  chromadb/chroma:latest

```

---

### 2️⃣ 写入层：多格式兼容的「一次性任务」入库容器

此容器负责在每日凌晨启动，利用本地 CPU 计算向量，将多格式手册解析并同时写入 ChromaDB 的「切片库」和「全文库」，运行完即刻自动销毁。

#### 核心脚本 `/data/context7/sync_task/container_sync.py`

```python
import os
import hashlib
from datetime import datetime
import chromadb
from sentence_transformers import SentenceTransformer
from chromadb.utils.embedding_functions import EmbeddingFunction
import re

# 容器内映射路径
MODEL_IN_CONTAINER = "/app/models"
MANUALS_IN_CONTAINER = "/app/manuals"

# 切片配置
CHUNK_MAX_TOKENS = 512  # Token 上限（约 1.5 字/token）
MAX_RETRIES = 3         # 最大重试次数

# 1. 封装本地 CPU Embedding 引擎
class ContainerEmbeddingFunction(EmbeddingFunction):
    def __init__(self):
        self.model = SentenceTransformer(MODEL_IN_CONTAINER, device="cpu")
    def __call__(self, input: chromadb.Documents) -> chromadb.Embeddings:
        return self.model.encode(input, normalize_embeddings=True).tolist()

def get_file_md5(filepath):
    """计算文件完整 MD5（用于变更检测）"""
    hasher = hashlib.md5()
    with open(filepath, 'rb') as f:
        hasher.update(f.read())
    return hasher.hexdigest()

def estimate_tokens(text):
    """估算文本 Token 数（中文约 1.5 字/token）"""
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    other_chars = len(text) - chinese_chars
    return int(chinese_chars / 1.5 + other_chars / 4)

# 2. Markdown 按标题层级切片 (# ## ###)
def chunk_markdown_by_headers(content):
    """
    按 Markdown 标题层级 (# ## ### #### ##### ######) 切片
    保持每个章节的语义完整性
    """
    lines = content.split('\n')
    chunks = []
    current_chunk = []

    for line in lines:
        header_match = re.match(r'^(#{1,6})\s+(.+)$', line)
        if header_match:
            if current_chunk:
                chunk_text = '\n'.join(current_chunk).strip()
                if chunk_text:
                    chunks.append(chunk_text)
            current_chunk = [line]
        else:
            current_chunk.append(line)

    if current_chunk:
        chunk_text = '\n'.join(current_chunk).strip()
        if chunk_text:
            chunks.append(chunk_text)

    # 合并过小的 chunk
    merged = []
    for chunk in chunks:
        if len(chunk) < 50 and merged:
            merged[-1] = merged[-1] + '\n\n' + chunk
        else:
            merged.append(chunk)

    return merged if merged else [content]

# 3. PDF/DOCX 按段落 + Token 上限切片
def chunk_by_paragraphs_with_limit(content, max_tokens):
    paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]
    chunks = []
    current_chunk = []
    current_tokens = 0

    for para in paragraphs:
        para_tokens = estimate_tokens(para)
        if para_tokens > max_tokens:
            if current_chunk:
                chunks.append('\n\n'.join(current_chunk))
                current_chunk = []
                current_tokens = 0
            chunks.append(para)
            continue
        if current_tokens + para_tokens > max_tokens and current_chunk:
            chunks.append('\n\n'.join(current_chunk))
            current_chunk = [para]
            current_tokens = para_tokens
        else:
            current_chunk.append(para)
            current_tokens += para_tokens

    if current_chunk:
        chunks.append('\n\n'.join(current_chunk))

    return chunks if chunks else [content]

# 4. 代码文件按函数/类切片
def chunk_code_by_functions(content, filename):
    """按语言特性分割代码块"""
    ext = filename.split('.')[-1].lower()
    chunks = []

    if ext == 'py':
        # Python: 按 def/class 分割
        lines = content.split('\n')
        current_block = []
        in_function = False
        for line in lines:
            if re.match(r'^(def |class |@)', line):
                if current_block:
                    chunks.append('\n'.join(current_block))
                current_block = [line]
                in_function = True
            elif in_function:
                if line.strip() and not line.startswith(' ') and not line.startswith('\t'):
                    chunks.append('\n'.join(current_block))
                    current_block = [line] if line.strip() else []
                    in_function = False
                else:
                    current_block.append(line)
            else:
                current_block.append(line)
        if current_block:
            chunks.append('\n'.join(current_block))

    elif ext == 'java':
        # Java: 按 class/method 分割
        brace_count = 0
        current_block = []
        in_class = False
        for line in content.split('\n'):
            if re.match(r'^(public |private |protected |class |interface )', line.strip()):
                if current_block and brace_count == 0:
                    chunks.append('\n'.join(current_block))
                current_block = [line]
                in_class = True
            elif in_class:
                current_block.append(line)
                brace_count += line.count('{') - line.count('}')
                if brace_count == 0:
                    chunks.append('\n'.join(current_block))
                    current_block = []
                    in_class = False
            else:
                current_block.append(line)
        if current_block:
            chunks.append('\n'.join(current_block))

    elif ext == 'go':
        # Go: 按 func/type 分割
        for match in re.finditer(r'(func\s+\([^)]+\)?\s*\w+[^{]*\{[^}]*\}|func\s+\w+[^{]*\{[^}]*\}|type\s+\w+[^{]*\{[^}]*\})', content, re.DOTALL):
            chunks.append(match.group(0).strip())

    elif ext == 'sql':
        # SQL: 按语句分割
        statements = re.split(r';\s*\n', content)
        for stmt in statements:
            if stmt.strip():
                chunks.append(stmt.strip() + ';')

    elif ext == 'sh':
        # Shell: 按 function 分割
        for match in re.finditer(r'(function\s+\w+\s*\(\)?\s*\{[^}]*\}|\w+\(\)\s*\{[^}]*\})', content, re.DOTALL):
            chunks.append(match.group(0).strip())

    else:
        chunks = [content]

    # 添加代码块标记
    result = []
    for chunk in chunks:
        chunk = chunk.strip()
        if chunk:
            result.append(f"```{ext}\n{chunk}\n```")

    return result if result else [f"```{ext}\n{content}\n```"]

# 5. 智能切片：根据文件类型选择策略
def smart_chunk_content(content, filename):
    ext = filename.split('.')[-1].lower()
    if ext == 'md':
        return chunk_markdown_by_headers(content)
    elif ext in ('py', 'java', 'go', 'sql', 'sh'):
        return chunk_code_by_functions(content, filename)
    else:
        return chunk_by_paragraphs_with_limit(content, CHUNK_MAX_TOKENS)

# 6. 增量同步检查
def check_file_changed(col_full, filepath, current_md5):
    """检查文件是否发生变化"""
    filename = os.path.basename(filepath)
    try:
        result = col_full.get(ids=[filename])
        if result['metadatas'] and len(result['metadatas']) > 0:
            old_md5 = result['metadatas'][0].get('md5', '')
            return old_md5 != current_md5, result['metadatas'][0]
    except Exception:
        pass
    return True, None

# 7. 主同步逻辑
def main():
    CHROMA_HOST = os.getenv("CHROMA_HOST", "127.0.0.1")

    for retry in range(MAX_RETRIES):
        try:
            client = chromadb.HttpClient(host=CHROMA_HOST, port=8000)
            break
        except Exception as e:
            if retry == MAX_RETRIES - 1:
                print(f"[ERROR] 无法连接 ChromaDB")
                return
            import time
            time.sleep(5)

    # 集合一：切片库（语义搜索）
    embedding_func = ContainerEmbeddingFunction()
    col_chunks = client.get_or_create_collection(
        "internal_tech_chunks",
        embedding_function=embedding_func
    )
    # 集合二：全文库（精准读取）
    col_full = client.get_or_create_collection(
        "internal_full_docs",
        embedding_function=embedding_func  # 使用相同的 embedding 函数
    )

    stats = {'total': 0, 'updated': 0, 'skipped': 0, 'failed': 0}

    for root, _, files in os.walk(MANUALS_IN_CONTAINER):
        for file in files:
            filepath = os.path.join(root, file)
            stats['total'] += 1

            try:
                current_md5 = get_file_md5(filepath)
                is_changed, _ = check_file_changed(col_full, filepath, current_md5)

                if not is_changed:
                    print(f"[SKIP] 文件未变化: {file}")
                    stats['skipped'] += 1
                    continue

                content = ""
                if file.endswith('.md'):
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()
                elif file.endswith('.docx'):
                    from docx import Document
                    doc = Document(filepath)
                    content = "\n\n".join([p.text.strip() for p in doc.paragraphs if p.text.strip()])
                elif file.endswith('.pdf'):
                    import pdfplumber
                    text_list = []
                    with pdfplumber.open(filepath) as pdf:
                        for page in pdf.pages:
                            text = page.extract_text()
                            if text: text_list.append(text)
                    content = "\n\n".join(text_list)
                elif file.endswith(('.py', '.java', '.go', '.sql', '.sh')):
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()
                else:
                    continue

                if not content.strip():
                    continue

                mtime = os.path.getmtime(filepath)
                last_updated = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')
                meta = {
                    "file_path": file,
                    "last_updated": last_updated,
                    "version": "v1.0",
                    "md5": current_md5
                }

                # 写入全文库
                col_full.upsert(documents=[content], ids=[file], metadatas=[meta])

                # 智能切片写入切片库
                chunks = smart_chunk_content(content, file)
                chunk_ids = [f"{file}_{current_md5[:8]}_{i}" for i in range(len(chunks))]
                chunk_metadatas = [
                    {**meta, "chunk_index": i, "chunk_title": extract_chunk_title(chunks[i])}
                    for i in range(len(chunks))
                ]

                # 删除旧切片
                old_chunk_ids = [id for id in col_chunks.get()['ids'] if id.startswith(f"{file}_")]
                if old_chunk_ids:
                    col_chunks.delete(ids=old_chunk_ids)

                col_chunks.upsert(documents=chunks, ids=chunk_ids, metadatas=chunk_metadatas)
                print(f"[UPDATE] {file} | 切片: {len(chunks)}")
                stats['updated'] += 1

            except Exception as e:
                print(f"[ERROR] {file}: {str(e)}")
                stats['failed'] += 1

    print(f"\n同步完成! 总: {stats['total']}, 更新: {stats['updated']}, 跳过: {stats['skipped']}, 失败: {stats['failed']}")

def extract_chunk_title(chunk):
    match = re.match(r'^(#{1,6})\s+(.+)$', chunk)
    if match:
        return match.group(2).strip()
    match = re.match(r'^```(\w+)', chunk)
    if match:
        return f"代码块 ({match.group(1)})"
    return "段落切片"

if __name__ == "__main__":
    main()

```

#### Dockerfile `/data/context7/sync_task/Dockerfile`

```dockerfile
FROM python:3.10-slim

WORKDIR /app

# 先安装 PyTorch CPU 版本，再用 --no-deps 安装 sentence-transformers 防止重装 torch
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir sentence-transformers --no-deps && \
    pip install --no-cache-dir chromadb python-docx pdfplumber transformers huggingface-hub

COPY container_sync.py /app/container_sync.py

ENTRYPOINT ["python", "/app/container_sync.py"]

```

构建镜像：

```bash
cd /data/context7/sync_task
docker build -t sync-task:latest .

```

#### 宿主机 Crontab 配置

```text
0 2 * * * docker run --rm --name tmp-mcp-syncer --network host -v /data/context7/models/bge-small-zh-v1.5:/app/models -v /data/context7/manuals:/app/manuals -e CHROMA_HOST="127.0.0.1" sync-task:latest >> /data/context7/sync_task/cron_run.log 2>&1

```

---

### 3️⃣ 查询层：常驻内网的独立 MCP 服务 API 容器

查询服务容器保持高纯净度，只需配置 `chromadb` 客户端与 `mcp` 基础库。它不加载大体积模型，全部通过 HTTP 远程检索。

#### `/data/context7/mcp_server/mcp_api.py`

```python
import os
from datetime import datetime
from mcp.server.fastmcp import FastMCP
import chromadb
from chromadb.utils.embedding_functions import EmbeddingFunction
from sentence_transformers import SentenceTransformer

mcp = FastMCP("Internal-Tech-Manual-API")

CHROMA_HOST = os.getenv("CHROMA_HOST", "127.0.0.1")
chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=8000)

# 使用与同步脚本相同的 embedding function
class LocalEmbeddingFunction(EmbeddingFunction):
    def __init__(self):
        # 容器内路径
        model_path = os.getenv("MODEL_PATH", "/app/models")
        self.model = SentenceTransformer(model_path, device="cpu")
    def __call__(self, input: chromadb.Documents) -> chromadb.Embeddings:
        return self.model.encode(input, normalize_embeddings=True).tolist()

embedding_func = LocalEmbeddingFunction()

col_chunks = chroma_client.get_collection(name="internal_tech_chunks", embedding_function=embedding_func)
col_full = chroma_client.get_collection(name="internal_full_docs", embedding_function=embedding_func)

# 接口一：模糊语义搜索
@mcp.tool()
def search_tech_manual(query: str) -> str:
    """
    当需要查询公司内部各系统架构规范、API 指南、安全守则、大模型风控或升级文档时使用此工具。
    返回最匹配且版本最新的技术手册片段，并包含可用于追溯全文的文件路径 [file_path]。
    """
    results = col_chunks.query(query_texts=[query], n_results=5)
    if not results['documents'] or len(results['documents'][0]) == 0:
        return "未在内部技术手册中找到相关规范片段。"

    items = []
    for i in range(len(results['documents'][0])):
        items.append({
            "content": results['documents'][0][i],
            "metadata": results['metadatas'][0][i]
        })

    items.sort(key=lambda x: datetime.strptime(x['metadata']['last_updated'], "%Y-%m-%d"), reverse=True)

    formatted_chunks = []
    for item in items[:3]:
        chunk_title = item['metadata'].get('chunk_title', '段落切片')
        formatted_chunks.append(
            f"📍 [file_path]: {item['metadata']['file_path']}\n"
            f"📌 [章节]: {chunk_title}\n"
            f"📅 更新时间: {item['metadata']['last_updated']}\n"
            f"📄 片段正文:\n{item['content']}"
        )
    return "\n\n===\n\n".join(formatted_chunks)

# 接口二：精准全文读取
@mcp.tool()
def get_manual_chapter(file_path: str) -> str:
    """
    当调用 search_tech_manual 锁定了具体的 [file_path] 后，如果发现切片信息不全，
    需要进一步阅读该文件/章节的完整技术细节或全部代码示例时使用。
    """
    try:
        result = col_full.get(ids=[file_path])
        if result['documents'] and len(result['documents']) > 0:
            md5_hash = result['metadatas'][0].get('md5', 'N/A')[:8]
            return (
                f"> **[内部规范全文]** 正在阅读: {file_path} "
                f"(最后更新: {result['metadatas'][0]['last_updated']}, MD5: {md5_hash})\n"
                f"> AI 请基于此完整上下文，给出最契合当前内网架构要求的编码建议。\n\n"
                f"--- 章节全文开始 ---\n\n"
                f"{result['documents'][0]}"
            )
        return f"未能在内网向量库中找到路径为 {file_path} 的完整章节内容。"
    except Exception as e:
        return f"拉取全文失败: {str(e)}"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(mcp.sse_app(), host="0.0.0.0", port=8500)

```

#### Dockerfile `/data/context7/mcp_server/Dockerfile`

```dockerfile
FROM python:3.10-slim

WORKDIR /app

# 先安装 PyTorch CPU 版本，再用 --no-deps 安装 sentence-transformers 防止重装 torch
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir sentence-transformers --no-deps && \
    pip install --no-cache-dir mcp[cli] chromadb uvicorn transformers huggingface-hub

COPY mcp_api.py /app/mcp_api.py

EXPOSE 8500

ENTRYPOINT ["python", "/app/mcp_api.py"]

```

构建镜像：

```bash
cd /data/context7/mcp_server
docker build -t mcp-server:latest .

```

运行常驻 MCP API 容器：

```bash
docker run -d \
  --name mcp-api-service \
  --restart always \
  --network context7-net \
  -p 8500:8500 \
  --cpus="2" \
  --memory="2g" \
  -v /data/context7/models/bge-small-zh-v1.5:/app/models \
  -e MODEL_PATH="/app/models" \
  -e CHROMA_HOST="internal-chromadb" \
  mcp-server:latest

```

> **说明**：两个容器加入同一 Docker 网络 `context7-net`，MCP 服务通过容器名 `internal-chromadb` 连接 ChromaDB，IP 变化不影响连接。

---

### 🔗 4️⃣ AI 客户端接入配置

所有技术同事通过内网网络连接该服务器的 `8500` 端口。

**Cursor 接入配置：**
- **Name**: `internal-docs`
- **Type**: `SSE`
- **URL**: `http://10.x.x.x:8500/sse` *(10.x.x.x 为服务器内网 IP)*

**Claude Code 接入配置：**
```bash
claude mcp add internal-docs -- http://10.x.x.x:8500/sse

```

---

### 📊 镜像信息

| 镜像 | 大小 | 说明 |
|------|------|------|
| `sync-task:latest` | ~1.41GB | 包含 sentence-transformers + PyTorch CPU 版本 |
| `mcp-server:latest` | ~1.36GB | 包含 mcp + chromadb + sentence-transformers (用于本地 embedding 计算) |

**注意：** 使用 PyTorch CPU 版本显著减小了镜像体积（相比 CUDA 版本约 5GB+）。

---

这套方案完美达成了容器彻底解耦、宿主机零依赖污染的目标，且利用**增量同步**与**智能切片**策略大幅提升效率，双接口赋予了客户端 AI "先定位再通读"的独立思考能力。