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
    返回: [chunk_content, ...]
    """
    lines = content.split('\n')
    chunks = []
    current_chunk = []
    current_title = "文档开头"

    for line in lines:
        # 匹配 Markdown 标题 (1-6级)
        header_match = re.match(r'^(#{1,6})\s+(.+)$', line)

        if header_match:
            # 遇到新标题，保存当前 chunk
            if current_chunk:
                chunk_text = '\n'.join(current_chunk).strip()
                if chunk_text:
                    chunks.append(chunk_text)

            # 开始新 chunk（包含标题行）
            current_title = header_match.group(2).strip()
            current_chunk = [line]
        else:
            current_chunk.append(line)

    # 保存最后一个 chunk
    if current_chunk:
        chunk_text = '\n'.join(current_chunk).strip()
        if chunk_text:
            chunks.append(chunk_text)

    # 合并过小的 chunk（小于 50 字符的合并到上一个）
    merged = []
    for chunk in chunks:
        if len(chunk) < 50 and merged:
            merged[-1] = merged[-1] + '\n\n' + chunk
        else:
            merged.append(chunk)

    return merged if merged else [content]

# 3. 智能切片：根据文件类型选择策略
def smart_chunk_content(content, filename):
    """
    根据文件类型选择切片策略：
    - Markdown (.md): 按标题层级 (# ## ###) 切片
    - PDF/DOCX: 按段落 + Token 上限 (512) 切片
    - 代码文件 (.py .java .go .sql .sh): 按函数/类级别切片
    """
    ext = filename.split('.')[-1].lower()

    if ext == 'md':
        # Markdown: 按标题层级切片
        return chunk_markdown_by_headers(content)

    elif ext in ('py', 'java', 'go', 'sql', 'sh'):
        # 代码文件：按函数/类级别切片
        return chunk_code_by_functions(content, filename)

    else:
        # PDF/DOCX 及其他：按段落 + Token 上限切片
        return chunk_by_paragraphs_with_limit(content, CHUNK_MAX_TOKENS)

def chunk_by_paragraphs_with_limit(content, max_tokens):
    """
    PDF/DOCX 按段落 + Token 上限切片
    段落间保持完整，当累计 token 超过上限时开始新切片
    """
    paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]
    chunks = []
    current_chunk = []
    current_tokens = 0

    for para in paragraphs:
        para_tokens = estimate_tokens(para)

        # 如果当前段落本身超过上限，单独成为一个切片
        if para_tokens > max_tokens:
            if current_chunk:
                chunks.append('\n\n'.join(current_chunk))
                current_chunk = []
                current_tokens = 0
            chunks.append(para)
            continue

        # 累加段落，超过上限则开始新切片
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

def chunk_code_by_functions(content, filename):
    """
    代码文件按函数/类级别切片
    - Python (.py): 按 def/class 分割
    - Java (.java): 按 class/method 分割
    - Go (.go): 按 func/type 分割
    - SQL (.sql): 按 CREATE/INSERT/SELECT 分割
    - Shell (.sh): 按 function 分割
    """
    ext = filename.split('.')[-1].lower()
    chunks = []

    if ext == 'py':
        # Python: 按 def/class 分割
        lines = content.split('\n')
        current_block = []
        in_function = False
        indent_level = 0

        for line in lines:
            # 检测函数/类定义开始
            if re.match(r'^(def |class |@)', line):
                if current_block:
                    chunks.append('\n'.join(current_block))
                current_block = [line]
                in_function = True
                indent_level = len(line) - len(line.lstrip())
            elif in_function:
                # 检测块结束（缩进回到原级别或更少）
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
        # 简化处理：按大括号层级分割
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
        # SQL: 按语句分割（CREATE, INSERT, SELECT, UPDATE, DELETE）
        statements = re.split(r';\s*\n', content)
        for stmt in statements:
            if stmt.strip():
                chunks.append(stmt.strip() + ';')

    elif ext == 'sh':
        # Shell: 按 function 分割
        for match in re.finditer(r'(function\s+\w+\s*\(\)?\s*\{[^}]*\}|\w+\(\)\s*\{[^}]*\})', content, re.DOTALL):
            chunks.append(match.group(0).strip())

    else:
        # 未知类型：整体作为一个切片
        chunks = [content]

    # 过滤空切片，添加代码块标记
    result = []
    for chunk in chunks:
        chunk = chunk.strip()
        if chunk:
            result.append(f"```{ext}\n{chunk}\n```")

    return result if result else [f"```{ext}\n{content}\n```"]

# 4. 针对不同格式的解析器
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

# 5. 增量同步检查
def check_file_changed(col_full, filepath, current_md5):
    """
    检查文件是否发生变化
    返回: (is_changed, old_metadata)
    """
    filename = os.path.basename(filepath)
    try:
        result = col_full.get(ids=[filename])
        if result['metadatas'] and len(result['metadatas']) > 0:
            old_md5 = result['metadatas'][0].get('md5', '')
            return old_md5 != current_md5, result['metadatas'][0]
    except Exception:
        pass
    return True, None  # 新文件或查询失败，视为需要更新

# 6. 主同步逻辑（带重试和增量检测）
def main():
    CHROMA_HOST = os.getenv("CHROMA_HOST", "127.0.0.1")

    for retry in range(MAX_RETRIES):
        try:
            client = chromadb.HttpClient(host=CHROMA_HOST, port=8000)
            break
        except Exception as e:
            if retry == MAX_RETRIES - 1:
                print(f"[ERROR] 无法连接 ChromaDB，重试 {MAX_RETRIES} 次后失败: {e}")
                return
            print(f"[WARN] ChromaDB 连接失败，第 {retry + 1} 次重试...")
            import time
            time.sleep(5)

    # 集合一：切片库（语义搜索）
    col_chunks = client.get_or_create_collection(
        "internal_tech_chunks",
        embedding_function=ContainerEmbeddingFunction()
    )
    # 集合二：全文库（精准读取）
    col_full = client.get_or_create_collection("internal_full_docs")

    print(f"[{datetime.now()}] 开始扫描手册目录进行全格式解析...")

    stats = {
        'total': 0,
        'updated': 0,
        'skipped': 0,
        'failed': 0
    }

    for root, _, files in os.walk(MANUALS_IN_CONTAINER):
        for file in files:
            filepath = os.path.join(root, file)
            stats['total'] += 1

            try:
                # 计算当前文件 MD5
                current_md5 = get_file_md5(filepath)

                # 增量检测：检查是否需要更新
                is_changed, old_meta = check_file_changed(col_full, filepath, current_md5)

                if not is_changed:
                    print(f"[SKIP] 文件未变化: {file} (MD5: {current_md5[:8]})")
                    stats['skipped'] += 1
                    continue

                # 解析文件内容
                content = ""
                if file.endswith('.md'):
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()
                elif file.endswith('.docx'):
                    content = parse_docx(filepath)
                elif file.endswith('.pdf'):
                    content = parse_pdf(filepath)
                elif file.endswith(('.py', '.java', '.go', '.sql', '.sh')):
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()
                else:
                    continue

                if not content.strip():
                    continue

                # 构造元数据（包含完整 MD5）
                mtime = os.path.getmtime(filepath)
                last_updated = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')
                meta = {
                    "file_path": file,
                    "last_updated": last_updated,
                    "version": "v1.0",
                    "md5": current_md5  # 存储完整 MD5 用于增量检测
                }

                # A. 写入全文库
                col_full.upsert(
                    documents=[content],
                    ids=[file],
                    metadatas=[meta]
                )

                # B. 智能切片写入切片库
                chunks = smart_chunk_content(content, file)
                chunk_ids = [f"{file}_{current_md5[:8]}_{i}" for i in range(len(chunks))]
                chunk_metadatas = [
                    {**meta, "chunk_index": i, "chunk_title": extract_chunk_title(chunks[i])}
                    for i in range(len(chunks))
                ]

                # 先删除旧的切片（基于文件名前缀）
                old_chunk_ids = [id for id in col_chunks.get()['ids'] if id.startswith(f"{file}_")]
                if old_chunk_ids:
                    col_chunks.delete(ids=old_chunk_ids)

                # 写入新切片
                col_chunks.upsert(
                    documents=chunks,
                    ids=chunk_ids,
                    metadatas=chunk_metadatas
                )

                print(f"[UPDATE] {file} | MD5: {current_md5[:8]} | 切片: {len(chunks)}")
                stats['updated'] += 1

            except Exception as e:
                print(f"[ERROR] 处理文件失败 {file}: {str(e)}")
                stats['failed'] += 1

    # 同步统计报告
    print(f"\n{'='*50}")
    print(f"[{datetime.now()}] 同步完成!")
    print(f"  总文件数: {stats['total']}")
    print(f"  已更新: {stats['updated']}")
    print(f"  已跳过(未变化): {stats['skipped']}")
    print(f"  失败: {stats['failed']}")
    print(f"{'='*50}")

def extract_chunk_title(chunk):
    """提取切片标题（用于元数据）"""
    # 提取 Markdown 标题
    match = re.match(r'^(#{1,6})\s+(.+)$', chunk)
    if match:
        return match.group(2).strip()
    # 提取代码块语言标识
    match = re.match(r'^```(\w+)', chunk)
    if match:
        lang = match.group(1)
        return f"代码块 ({lang})"
    return "段落切片"

if __name__ == "__main__":
    main()