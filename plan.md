根据你的最终需求，这是一套**完全容器化、宿主机零污染、支持多格式文件、且具备「语义切片搜索」与「精准全文读取」双工具接口**的完整内网 MCP 方案。

整套方案不依赖 Docker Compose，全部使用原生 `docker run` 命令，通过**读写分离**与双集合（Chunks + Full）的设计，确保在纯 CPU 内网环境下的高效、稳定运行。

---

### 📂 最终项目目录结构（宿主机）

在宿主机上创建以下纯文本/数据目录，**宿主机无需配置任何 Python 环境或依赖**：

```text
/data/context7/
├── models/
│   └── bge-small-zh-v1.5/       # 离线 Embedding 模型文件（需提前下载放入）
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

### 1️⃣ 存储层：拉起独立 ChromaDB 数据库容器

直接在宿主机运行以下命令，作为整套系统的标准向量底座：

```bash
docker run -d \
  --name internal-chromadb \
  --restart always \
  --cpus="4" \
  --memory="4g" \
  -p 8000:8000 \
  -v /data/context7/chroma_data:/chroma/chroma \
  chromadb/chroma:latest

```

---

### 2️⃣ 写入层：多格式兼容的「一次性任务」入库容器

此容器负责在每日凌晨启动，利用本地 CPU 计算向量，将多格式手册解析并同时写入 ChromaDB 的「切片库」和「全文库」，运行完即刻自动销毁。

#### 1. 编写入库核心脚本 `/data/context7/sync_task/container_sync.py`

```python
import os
import hashlib
from datetime import datetime
import chromadb
from sentence_transformers import SentenceTransformer
from chromadb.utils.embedding_functions import EmbeddingFunction

# 容器内映射路径
MODEL_IN_CONTAINER = "/app/models"
MANUALS_IN_CONTAINER = "/app/manuals"

# 1. 封装本地 CPU Embedding 引擎
class ContainerEmbeddingFunction(EmbeddingFunction):
    def __init__(self):
        self.model = SentenceTransformer(MODEL_IN_CONTAINER, device="cpu")
    def __call__(self, input: chromadb.Documents) -> chromadb.Embeddings:
        return self.model.encode(input, normalize_embeddings=True).tolist()

def get_file_md5(filepath):
    hasher = hashlib.md5()
    with open(filepath, 'rb') as f:
        hasher.update(f.read())
    return hasher.hexdigest()

# 2. 针对不同格式的本地解析器（无需宿主机安装依赖）
def parse_docx(filepath):
    from docx import Document
    doc = Document(filepath)
    return "\n\n".join([p.text.strip() for p in doc.paragraphs if p.text.strip()])

def parse_pdf(filepath):
    import pdfplumber
    text_list = []
    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text: text_list.append(text)
    return "\n\n".join(text_list)

def parse_code(filepath, filename):
    with open(filepath, 'r', encoding='utf-8') as f:
        code_content = f.read()
    ext = filename.split('.')[-1]
    return f"内部代码/示例脚本 ({filename}):\n```{ext}\n{code_content}\n```"

# 3. 主同步逻辑
def main():
    CHROMA_HOST = os.getenv("CHROMA_HOST", "127.0.0.1")
    client = chromadb.HttpClient(host=CHROMA_HOST, port=8000)
    
    # 集合一：存放切片（用于模糊搜索）
    col_chunks = client.get_or_create_collection("internal_tech_chunks", embedding_function=ContainerEmbeddingFunction())
    # 集合二：存放未切片的完整全文（用于精准全文读取，由 Chroma 自动管理，无需传 Embedding 模型）
    col_full = client.get_or_create_collection("internal_full_docs")
    
    print(f"[{datetime.now()}] 开始扫描手册目录进行全格式解析...")
    
    for root, _, files in os.walk(MANUALS_IN_CONTAINER):
        for file in files:
            filepath = os.path.join(root, file)
            content = ""
            
            try:
                if file.endswith('.md'):
                    with open(filepath, 'r', encoding='utf-8') as f: content = f.read()
                elif file.endswith('.docx'):
                    content = parse_docx(filepath)
                elif file.endswith('.pdf'):
                    content = parse_pdf(filepath)
                elif file.endswith(('.py', '.java', '.go', '.sql', '.sh')):
                    content = parse_code(filepath, file)
                else:
                    continue
                
                if not content.strip(): continue
                
                # 构造元数据
                md5_str = get_file_md5(filepath)[:8]
                mtime = os.path.getmtime(filepath)
                last_updated = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')
                
                meta = {"file_path": file, "last_updated": last_updated, "version": "v1.0"}
                
                # A. 写入全文库 (以文件名 file 作为唯一 ID)
                col_full.upsert(documents=[content], ids=[file], metadatas=[meta])
                
                # B. 写入切片库 (按段落切片)
                chunks = [c.strip() for c in content.split("\n\n") if c.strip()]
                chunk_ids = [f"{file}_{md5_str}_{i}" for i in range(len(chunks))]
                chunk_metadatas = [meta for _ in chunks]
                
                col_chunks.upsert(documents=chunks, ids=chunk_ids, metadatas=chunk_metadatas)
                print(f"成功同步文件: {file} (已切分为 {len(chunks)} 个片段)")
                
            except Exception as e:
                print(f"解析文件失败 {file}: {str(e)}")

if __name__ == "__main__":
    main()

```

#### 2. 编写定时任务容器的 `/data/context7/sync_task/Dockerfile`

```dockerfile
FROM python:3.10-slim

WORKDIR /app

# 一次性安装所有格式解析所需的库（内网环境可指定私有源）
RUN pip install --no-cache-dir sentence-transformers chromadb python-docx pdfplumber

COPY container_sync.py /app/container_sync.py

ENTRYPOINT ["python", "/app/container_sync.py"]

```

在当前目录下构建入库镜像：

```bash
docker build -t internal-mcp-syncer:1.0 .

```

#### 📅 3. 宿主机 Crontab 配置（零依赖触发）

在宿主机终端输入 `crontab -e`，添加以下行（每天凌晨 2:00 自动拉起容器，**算完自动销毁 `--rm**`）：

```text
0 2 * * * docker run --rm --name tmp-mcp-syncer --network host -v /data/context7/models/bge-small-zh-v1.5:/app/models -v /data/context7/manuals:/app/manuals -e CHROMA_HOST="127.0.0.1" internal-mcp-syncer:1.0 >> /data/context7/sync_task/cron_run.log 2>&1

```

---

### 3️⃣ 查询层：常驻内网的独立 MCP 服务 API 容器

查询服务容器保持高纯净度，只需配置 `chromadb` 客户端与 `mcp` 基础库。它不加载大体积模型，全部通过 HTTP 远程检索。

#### 1. 编写 `/data/context7/mcp_server/mcp_api.py`

```python
import os
from datetime import datetime
from mcp.server.fastmcp import FastMCP
import chromadb

mcp = FastMCP("Internal-Tech-Manual-API")

CHROMA_HOST = os.getenv("CHROMA_HOST", "127.0.0.1")
chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=8000)

col_chunks = chroma_client.get_collection(name="internal_tech_chunks")
col_full = chroma_client.get_collection(name="internal_full_docs")

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
        items.append({"content": results['documents'][0][i], "metadata": results['metadatas'][0][i]})
    
    # 内存按最新时间重排
    items.sort(key=lambda x: datetime.strptime(x['metadata']['last_updated'], "%Y-%m-%d"), reverse=True)
    
    formatted_chunks = []
    for item in items[:3]:  # 取前 3 条最精准的最新切片
        formatted_chunks.append(
            f"📍 [file_path]: {item['metadata']['file_path']}\n"
            f"📅 更新时间: {item['metadata']['last_updated']}\n"
            f"📄 片段正文:\n{item['content']}"
        )
    return "\n\n===\n\n".join(formatted_chunks)

# 接口二：精准全文读取
@mcp.tool()
def get_manual_chapter(file_path: str) -> str:
    """
    当调用 search_tech_manual 锁定了具体的 [file_path] 后，如果发现切片信息不全，需要进一步阅读该文件/章节的完整技术细节或全部代码示例时使用。
    """
    try:
        result = col_full.get(ids=[file_path])
        if result['documents'] and len(result['documents']) > 0:
            return (
                f"> **[内部规范全文]** 正在阅读: {file_path} (最后更新: {result['metadatas'][0]['last_updated']})\n"
                f"> AI 请基于此完整上下文，给出最契合当前内网架构要求的编码或修改建议。\n\n"
                f"--- 章节全文开始 ---\n\n"
                f"{result['documents'][0]}"
            )
        return f"未能在内网向量库中找到路径为 {file_path} 的完整章节内容。"
    except Exception as e:
        return f"拉取全文失败: {str(e)}"

if __name__ == "__main__":
    mcp.run(transport="sse", port=8500)

```

#### 2. 编写查询服务的 `/data/context7/mcp_server/Dockerfile`

```dockerfile
FROM python:3.10-slim

WORKDIR /app

RUN pip install --no-cache-dir mcp[cli] chromadb

COPY mcp_api.py /app/mcp_api.py

EXPOSE 8500

ENTRYPOINT ["python", "/app/mcp_api.py"]

```

在当前目录下构建 API 镜像：

```bash
docker build -t internal-mcp-api:1.0 .

```

#### 3. 运行常驻 MCP API 容器

```bash
docker run -d \
  --name mcp-api-service \
  --restart always \
  --network host \
  --cpus="2" \
  --memory="2g" \
  internal-mcp-api:1.0

```

---

### 🔗 4️⃣ AI 客户端最终网络接入

所有技术同事通过内网网络连接该服务器的 `8500` 端口。

* **Cursor 接入配置：**
进入设置，添加全局 MCP 服务：
* **Name**: `internal-docs`
* **Type**: `SSE`
* **URL**: `http://10.x.x.x:8500/sse` *(10.x.x.x 为你部署该容器的服务器内网 IP)*


* **Claude Code 接入配置：**
在终端中直接绑定该内网端点：
```bash
claude mcp add internal-docs -- http://10.x.x.x:8500/sse

```



这套方案完美达成了容器彻底解耦、宿主机零依赖污染的目标，且利用双接口赋予了客户端 AI “先定位再通读”的独立思考能力。

你准备先拿几个什么样的核心框架文档或规范文件来做第一批的内网导入测试？
