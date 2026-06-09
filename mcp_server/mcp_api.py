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

    # 内存按最新时间重排
    items.sort(key=lambda x: datetime.strptime(x['metadata']['last_updated'], "%Y-%m-%d"), reverse=True)

    formatted_chunks = []
    for item in items[:3]:  # 取前 3 条最精准的最新切片
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
    当调用 search_tech_manual 锁定了具体的 [file_path] 后，如果发现切片信息不全，需要进一步阅读该文件/章节的完整技术细节或全部代码示例时使用。
    """
    try:
        result = col_full.get(ids=[file_path])
        if result['documents'] and len(result['documents']) > 0:
            md5_hash = result['metadatas'][0].get('md5', 'N/A')[:8]
            return (
                f"> **[内部规范全文]** 正在阅读: {file_path} (最后更新: {result['metadatas'][0]['last_updated']}, MD5: {md5_hash})\n"
                f"> AI 请基于此完整上下文，给出最契合当前内网架构要求的编码或修改建议。\n\n"
                f"--- 章节全文开始 ---\n\n"
                f"{result['documents'][0]}"
            )
        return f"未能在内网向量库中找到路径为 {file_path} 的完整章节内容。"
    except Exception as e:
        return f"拉取全文失败: {str(e)}"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(mcp.sse_app(), host="0.0.0.0", port=8500)