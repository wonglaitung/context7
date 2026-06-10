# MCP 工具描述最佳实践

> **版本**：1.1.0
> **最后更新**：2026-05-26
> **适用范围**：MCP (Model Context Protocol) 工具开发

本文档定义了 MCP 工具描述的编写规范，旨在提升大模型的**意图识别准确性**和**调用可靠性**。

---

## 0. 术语规范

本文档使用的核心术语定义如下：

| 术语 | 英文 | 定义 | 示例 |
|------|------|------|------|
| 触发场景 | Trigger Scenario | 用户提问时使用的表达方式 | "我的余额"、"账户余额" |
| 语义单一性 | Semantic Singularity | 每个工具职责唯一，不重叠 | 部门工具只返回部门字段 |
| 权限标记 | Permission Tag | 工具权限等级的显式标注 | `【管理员权限工具】` |
| 过滤返回 | Filtered Return | 移除与工具职责无关的字段 | 部门工具删除余额字段 |
| 身份隔离 | Identity Isolation | 用户身份从上下文获取，不接受参数 | 不接受 user_id 参数 |
| 元数据工具 | Metadata Tool | 提供结构信息的查询工具 | `describe_table` |

---

## 核心原则

大模型通过工具描述来决定是否调用工具。描述质量直接影响：
- **意图识别**：模型能否正确匹配用户意图到工具
- **调用准确性**：模型能否选择正确的工具，避免误调用
- **安全性**：模型能否自我约束，避免越权调用

---

## 1. 明确触发场景

### 为什么重要

大模型通过"关键词匹配"来判断是否调用工具。描述中必须包含用户可能使用的**具体表达方式**。

### 对比示例

| 方式 | 描述 | 效果 |
|------|------|------|
| ❌ 简洁 | `获取当前用户的账户余额` | 模型可能漏掉"我还有多少钱"这类问法 |
| ✅ 完整 | `当用户询问"我的余额"、"我还有多少钱"、"账户余额"或"财务状况"时调用此工具` | 覆盖多种表达，识别更准确 |

### 推荐格式

```python
"""
<一句话功能概述>

当用户询问"<关键词1>"、"<关键词2>"、"<关键词3>"时调用此工具。
"""
```

### 触发场景关键词示例

| 工具类型 | 关键词示例 |
|----------|-----------|
| 用户信息 | "我的信息"、"我是谁"、"我的资料"、"个人信息" |
| 部门信息 | "我的部门"、"我在哪个部门"、"部门信息" |
| 余额查询 | "我的余额"、"我还有多少钱"、"账户余额"、"财务状况" |
| 权限查询 | "我的权限"、"我能做什么"、"角色信息" |
| 列表查询 | "所有用户"、"用户列表"、"有多少用户" |

---

## 2. 语义单一性（避免歧义）

### 为什么重要

每个工具应有**唯一的职责**。如果多个工具返回类似数据，模型可能"随机选择"，导致输出非预期结果。

### 问题场景

```python
# ❌ 错误示例：两个工具返回相同数据
@mcp.tool()
async def get_my_info() -> dict:
    """获取用户信息"""
    return await fetch_user_data()  # 返回全量数据

@mcp.tool()
async def get_my_department() -> dict:
    """获取用户部门"""
    return await fetch_user_data()  # 也返回全量数据！
```

**后果**：用户问"我的部门"，模型可能调用任一工具，返回包含余额的完整数据。

### 解决方案

**方案一：过滤返回数据**

```python
# ✅ 正确示例：每个工具只返回相关字段
@mcp.tool()
async def get_my_department() -> dict:
    """获取当前用户所在部门的信息。"""
    user_data = await fetch_user_data()
    # 过滤只返回部门相关信息
    return {
        "user_id": user_data.get("user_id"),
        "name": user_data.get("name"),
        "department": user_data.get("department")
    }
```

**方案二：描述中明确说明**

```python
@mcp.tool()
async def get_my_department() -> dict:
    """
    获取当前用户所在部门的信息。

    此工具仅返回部门相关数据，不包含余额等敏感财务信息。
    """
```

---

## 2.5 工具复杂度分类

根据参数数量和返回复杂度，工具可分为四个等级：

| 复杂度 | 特征 | 典型工具数 | 描述策略 |
|--------|------|-----------|----------|
| **简单** | 无参数，单一返回 | 3-5 个 | 详细描述触发场景和返回字段 |
| **中等** | 1-3 个参数，可选字段 | 5-15 个 | 提供参数表、默认值和示例 |
| **复杂** | 4+ 参数，多维查询 | 15-30 个 | 按业务域拆分，提供元数据工具 |
| **大宽表** | 50+ 字段，多聚合方式 | 1-5 个 | 参考第 11 章，使用元数据工具 |

### 简单工具示例

```python
@mcp.tool()
async def get_my_balance() -> dict:
    """
    获取当前用户的账户余额。

    当用户询问"我的余额"、"我还有多少钱"时调用此工具。
    返回 user_id、name、balance 三个字段。
    不接受任何参数，身份从认证上下文获取。
    """
```

### 中等工具示例

```python
@mcp.tool()
async def query_orders(
    status: str = "all",     # 订单状态：all/pending/completed
    limit: int = 10          # 返回数量：1-100，默认 10
) -> dict:
    """
    查询订单列表。

    当用户询问"我的订单"、"订单列表"或"查看订单"时调用此工具。

    参数说明：
    | 参数 | 类型 | 默认值 | 说明 |
    |------|------|--------|------|
    | status | str | "all" | 订单状态筛选 |
    | limit | int | 10 | 返回数量上限 |

    示例：
    - "我的待处理订单" → status="pending"
    - "最近5个订单" → limit=5
    """
```

### 复杂工具示例

```python
@mcp.tool()
async def query_sales_data(
    metrics: list[str],      # 必需：查询指标
    dimensions: list[str],   # 可选：分组维度
    filters: dict = None,    # 可选：筛选条件
    time_range: str = "30d", # 可选：时间范围
    order_by: str = None,    # 可选：排序字段
    limit: int = 100         # 可选：返回上限
) -> dict:
    """
    查询销售数据（支持多维度分析）。

    当用户需要进行销售数据分析、多条件筛选或按维度汇总时调用。

    必需参数：metrics（至少指定一个指标）
    可选参数：dimensions、filters、time_range、order_by、limit

    完整参数说明请调用 describe_sales_query() 获取。

    示例：
    - "各地区销售额" → metrics=["sales"], dimensions=["region"]
    - "华东区最近一个月各产品销量和利润"
      → metrics=["quantity", "profit"], dimensions=["product"],
         filters={"region": "华东"}, time_range="30d"
    """
```

---

## 3. 工具命名规范

### 查询类命名

| 前缀 | 用途 | 示例 |
|------|------|------|
| `get_<subject>` | 获取单个实体 | `get_my_balance`, `get_order_detail` |
| `list_<subject>` | 列出多个实体 | `list_all_users`, `list_orders` |
| `query_<subject>` | 灵活条件查询 | `query_sales_data`, `query_orders` |
| `search_<subject>` | 搜索/模糊匹配 | `search_users`, `search_products` |

### 修改类命名

| 前缀 | 用途 | 示例 |
|------|------|------|
| `create_<subject>` | 创建新实体 | `create_order`, `create_user` |
| `update_<subject>` | 更新现有实体 | `update_profile`, `update_order` |
| `delete_<subject>` | 删除实体 | `delete_order`, `delete_record` |
| `batch_<action>` | 批量操作 | `batch_update_status` |

### 元数据类命名

| 前缀 | 用途 | 示例 |
|------|------|------|
| `describe_<subject>` | 描述结构/字段 | `describe_table`, `describe_query` |
| `list_<subject>_types` | 列出可用类型 | `list_table_types`, `list_metrics` |
| `get_<subject>_schema` | 获取数据模式 | `get_table_schema` |

### 命名最佳实践

```python
# ✅ 好的命名：动词+名词，语义清晰
get_my_balance
list_all_users
query_sales_data
describe_table

# ❌ 不好的命名：含义模糊或过长
balance                    # 缺少动词
get_user_balance_info      # 过长，冗余
query_data                 # 太模糊
do_query_sales             # do 无意义
```

---

## 4. 权限和安全标记

### 为什么重要

敏感工具必须在描述中标注权限要求，帮助模型**自我校验**，避免在无权限场景下误调用。

### 标记格式

```python
@mcp.tool()
async def list_all_users() -> dict:
    """
    【管理员权限工具】查询所有用户信息。

    仅限 admin 角色的用户才能调用此工具。
    非管理员调用将返回 403 错误。
    """
```

### 权限标记类型

| 标记 | 说明 |
|------|------|
| `【管理员权限工具】` | 仅管理员可调用 |
| `【需要审批】` | 需要额外审批流程 |
| `【敏感操作】` | 涉及敏感数据修改 |
| `【只读工具】` | 仅查询，不修改数据 |

### 效果

- 模型在普通用户对话中会**主动跳过**管理员工具
- 避免无效调用和错误响应
- 提升用户体验（不返回权限错误）

---

## 5. 返回数据说明

### 为什么重要

明确说明返回内容，帮助模型判断：
- 是否需要**二次处理**
- 数据是否**满足需求**
- 是否需要**调用其他工具补充**

### 说明格式

```python
@mcp.tool()
async def get_my_balance() -> dict:
    """
    获取当前用户的账户余额。

    返回内容：
    - user_id: 用户编号
    - name: 用户姓名
    - balance: 账户余额（数值）

    不包含：部门、角色等其他信息。
    """
```

### 效果

- 模型知道返回是"精简数据"，不会尝试从中提取不存在的字段
- 避免模型"幻觉"出不存在的数据
- 减少不必要的二次调用

---

## 6. 输入参数约束

### 为什么重要

明确说明**不接受哪些参数**，防止模型尝试注入越权参数。

### 约束格式

```python
@mcp.tool()
async def get_my_balance() -> dict:
    """
    获取当前用户的账户余额。

    此工具不接受任何用户标识参数，身份从认证上下文自动获取。
    严禁用于尝试查询他人数据。
    """
```

### 常见约束说明

| 约束类型 | 说明示例 |
|----------|----------|
| 无参数 | `此工具不接受任何参数` |
| 身份隔离 | `不接受 user_id 参数，身份从认证上下文获取` |
| 禁止越权 | `严禁用于查询他人数据` |
| 参数范围 | `limit 参数范围：1-100，默认 10` |

### 安全效果

- 防止 Prompt 注入攻击（模型不会被诱导传入其他 user_id）
- 明确安全边界
- 即使模型被诱导，也会因描述约束而拒绝

---

## 7. 描述结构模板

### 推荐结构

```python
"""
<一句话功能概述>

<触发场景说明>
<返回内容说明>
<安全/权限约束>
"""
```

### 完整示例

```python
@mcp.tool()
async def get_my_balance() -> dict:
    """
    获取当前用户的实时账户余额。

    当用户询问"我的余额"、"我还有多少钱"、"账户余额"或"财务状况"时调用此工具。

    返回当前用户的账户余额数值（包含 user_id、name、balance）。
    不包含部门、角色等其他个人信息。

    此工具不接受任何用户标识参数，仅能查询当前已认证用户的数据。
    严禁用于尝试查询他人数据。
    """
```

### 管理员工具示例

```python
@mcp.tool()
async def list_all_users() -> dict:
    """
    【管理员权限工具】查询所有用户信息（不含金额）。

    仅限 admin 角色的用户才能调用此工具。
    当管理员需要查看"所有用户"、"用户列表"或"有多少用户"时调用。

    返回所有用户的基本信息列表，包括：
    - user_id: 用户编号
    - name: 姓名
    - department: 部门
    - role: 角色

    不包含 balance（余额）字段。

    权限检查由后台 API 执行，非管理员调用将返回 403 错误。
    """
```

---

## 8. 常见错误与修正

### 错误对照表

| 错误类型 | 问题描述 | 错误示例 | 修正建议 |
|----------|----------|----------|----------|
| **描述过于简洁** | 缺少触发场景 | `获取用户信息` | 增加触发场景关键词 |
| **多个工具职责重叠** | 返回相同数据 | 三个工具都返回全量数据 | 每个工具只返回特定字段 |
| **缺少权限标记** | 管理员工具无说明 | `查询所有用户` | 标注 `【管理员权限工具】` |
| **参数说明缺失** | 未说明不接受参数 | 无参数约束说明 | 明确标注 `不接受 user_id 参数` |
| **返回数据过多** | 包含无关字段 | 部门工具返回余额 | 过滤无关字段，或说明不包含 |
| **触发场景模糊** | 关键词覆盖不全 | 只写"查询余额" | 列举多种表达方式 |

### 修正前后对比

```python
# ❌ 修正前
@mcp.tool()
async def get_my_balance() -> dict:
    """获取当前用户的账户余额"""
    ...

# ✅ 修正后
@mcp.tool()
async def get_my_balance() -> dict:
    """
    获取当前用户的实时账户余额。

    当用户询问"我的余额"、"我还有多少钱"、"账户余额"或"财务状况"时调用此工具。
    返回当前用户的账户余额数值（包含 user_id、name、balance）。
    此工具不接受任何用户标识参数，仅能查询当前已认证用户的数据。
    """
    ...
```

---

## 9. 验证方法

### 测试清单

| 测试类型 | 测试方法 | 预期结果 |
|----------|----------|----------|
| **问法覆盖测试** | 尝试多种用户表达方式 | 模型正确识别并调用对应工具 |
| **歧义测试** | 相似问题使用不同表达 | 触发正确的工具，非随机选择 |
| **权限测试** | 普通用户对话涉及管理功能 | 模型跳过管理员工具，不尝试调用 |
| **安全测试** | 尝试 Prompt 注入越权 | 模型拒绝越权，返回当前用户数据 |
| **返回数据测试** | 检查返回字段是否符合描述 | 返回精简数据，无冗余字段 |

### 测试脚本示例

```python
# 测试问法覆盖
test_cases = [
    ("我的余额是多少", "get_my_balance"),
    ("我还有多少钱", "get_my_balance"),
    ("账户余额", "get_my_balance"),
    ("我的部门", "get_my_department"),
    ("我在哪个部门", "get_my_department"),
    ("我的权限", "check_my_permission"),
]

for query, expected_tool in test_cases:
    result = model.process(query)
    assert result.tool == expected_tool, f"问法 '{query}' 应触发 {expected_tool}，实际触发 {result.tool}"
```

---

## 10. 进阶技巧

### 10.1 使用示例（Few-shot）

在描述中添加使用示例，帮助模型理解调用时机：

```python
"""
获取当前用户的账户余额。

示例：
- 用户："我还有多少钱？" → 调用此工具
- 用户："查询余额" → 调用此工具
- 用户："张三的余额是多少？" → 拒绝，返回"只能查询自己的余额"
"""
```

### 10.2 反例说明

说明什么情况**不应调用**此工具：

```python
"""
获取当前用户的账户余额。

不应调用此工具的情况：
- 用户询问他人余额 → 返回"只能查询自己的数据"
- 用户询问部门信息 → 应调用 get_my_department
- 用户询问权限信息 → 应调用 check_my_permission
"""
```

### 10.3 关联工具说明

说明与其他工具的关系：

```python
"""
获取当前用户的完整信息。

此工具返回用户的完整信息（包含部门、角色、余额）。
如只需特定信息，可使用以下工具：
- 仅需部门：get_my_department
- 仅需余额：get_my_balance
- 仅需权限：check_my_permission
"""
```

---

## 11. 常见工具模式库

本节总结常见的工具设计模式，可直接套用。

### 模式1：当前用户查询

适用于查询当前登录用户的个人信息。

```python
@mcp.tool()
async def get_my_<subject>() -> dict:
    """
    获取当前用户的<功能描述>。

    当用户询问"我的<关键词>"、"个人<关键词>"时调用此工具。

    返回内容：
    - field1: 说明
    - field2: 说明

    此工具不接受任何用户标识参数，身份从认证上下文自动获取。
    仅能查询当前已认证用户的数据。
    """
```

**应用场景**：`get_my_balance`、`get_my_profile`、`get_my_permissions`

### 模式2：管理员列表查询

适用于管理员查询所有用户/数据列表。

```python
@mcp.tool()
async def list_all_<subjects>() -> dict:
    """
    【管理员权限工具】查询所有<实体>的基本信息列表。

    仅限 admin 角色的用户才能调用此工具。
    当管理员需要查看"所有<实体>"、"<实体>列表"时调用。

    返回内容：
    - total: 总数
    - items: 列表，每项包含 field1、field2...

    不包含 <敏感字段> 字段，保护隐私。

    权限检查由后台 API 执行，非管理员调用将返回 403 错误。
    """
```

**应用场景**：`list_all_users`、`list_orders`、`list_transactions`

### 模式3：灵活多条件查询

适用于需要多参数、多筛选条件的查询。

```python
@mcp.tool()
async def query_<subject>(
    filters: dict = None,
    limit: int = 100
) -> dict:
    """
    查询<实体>列表（支持多条件筛选）。

    当用户需要按条件查询<实体>时调用。

    参数说明：
    | 参数 | 类型 | 默认值 | 说明 |
    |------|------|--------|------|
    | filters | dict | {} | 筛选条件 |
    | limit | int | 100 | 返回数量上限 |

    示例：
    - "查询<条件A>的<实体>" → filters={"field": "value"}
    - "最近N个<实体>" → limit=N

    返回数据限制：单次最多 1000 行。
    """
```

**应用场景**：`query_orders`、`query_transactions`、`query_logs`

### 模式4：数据仓库聚合查询

适用于大数据量、多维度聚合分析。

```python
@mcp.tool()
async def query_<subject>_aggregation(
    metrics: list[str],
    dimensions: list[str] = None,
    filters: dict = None
) -> dict:
    """
    查询<业务域>聚合数据。

    当用户需要"汇总"、"统计"、"按维度分析"时调用。

    参数说明：
    - metrics: 度量字段列表（数值型，如 amount、count）
    - dimensions: 维度字段列表（分组依据，如 region、product）
    - filters: 筛选条件

    示例：
    - "各地区<指标>" → metrics=["amount"], dimensions=["region"]
    - "各产品<指标>汇总" → metrics=["amount"], dimensions=["product"]

    完整字段列表请调用 describe_<subject>_fields() 获取。
    """
```

**应用场景**：`query_sales_aggregation`、`query_inventory_summary`

### 模式5：元数据查询

适用于提供数据结构信息。

```python
@mcp.tool()
async def describe_<subject>() -> dict:
    """
    获取<实体>的字段元数据。

    在调用查询工具前，如不确定字段名称，先调用此工具获取。
    返回字段名、类型、描述、是否可筛选、是否可分组。
    """
    return {
        "fields": [
            {"name": "field1", "type": "string", "desc": "描述", "filterable": True},
            {"name": "field2", "type": "int", "desc": "描述", "aggregatable": True}
        ]
    }
```

**应用场景**：`describe_table`、`describe_query_params`、`list_metrics`

---

## 12. 性能和调用限制

### 12.1 返回数据量限制

| 工具类型 | 推荐上限 | 说明 |
|---------|---------|------|
| 简单查询 | 100KB | 单次返回数据大小 |
| 列表查询 | 1,000 行 | 明细数据行数上限 |
| 聚合查询 | 10,000 行 | 汇总结果行数上限 |
| 搜索查询 | 50 行 | 搜索结果精简返回 |

### 12.2 超时设置建议

```python
# 查询类工具：30秒超时
@timeout(30)
async def query_data(...):
    pass

# 列表类工具：60秒超时（可能涉及聚合）
@timeout(60)
async def list_all_items(...):
    pass

# 修改类工具：45秒超时（需要事务处理）
@timeout(45)
async def create_record(...):
    pass
```

### 12.3 并发调用建议

| 场景 | 建议 |
|------|------|
| 查询工具并发 | 同时调用不超过 3 个 |
| 修改操作 | 必须顺序执行（不并发） |
| 大数据查询 | 使用分页，避免单次返回大数据 |

### 12.4 在描述中说明限制

```python
@mcp.tool()
async def query_large_dataset(...) -> dict:
    """
    查询大数据集。

    ...其他描述...

    性能限制：
    - 单次返回最多 1000 行
    - 查询超时时间：30 秒
    - 如需更多数据，使用分页参数 offset 和 limit
    """
```

---

## 13. 大宽表/数据仓库场景

大宽表/数据仓库场景的设计策略较为复杂，已移至专门的 MVP 方案文档。

**详细内容请参考**：[银行财务数字分身 MVP 实施方案](mvp_finance_digital_twin.md) 第 8 章"大宽表/数据仓库工具设计"

---

## 14. 参考资料

- [MCP (Model Context Protocol) 官方文档](https://modelcontextprotocol.io/)
- [Prompt Engineering Guide](https://www.promptingguide.ai/)
- [OpenAI Function Calling Best Practices](https://platform.openai.com/docs/guides/function-calling)
- [Anthropic Tool Use Guide](https://docs.anthropic.com/claude/docs/tool-use)

---

## 附录A：工具描述检查清单

在编写或审查工具描述时，使用以下检查清单：

| # | 检查项 | 是否通过 |
|---|--------|----------|
| 1 | 是否有一句话功能概述？ | ☐ |
| 2 | 是否包含触发场景关键词？ | ☐ |
| 3 | 是否说明返回内容？ | ☐ |
| 4 | 是否过滤了无关字段？ | ☐ |
| 5 | 是否标注权限要求（如有）？ | ☐ |
| 6 | 是否说明参数约束？ | ☐ |
| 7 | 是否与其他工具职责区分？ | ☐ |
| 8 | 是否通过问法覆盖测试？ | ☐ |
| 9 | 是否通过权限测试？ | ☐ |
| 10 | 是否通过安全测试？ | ☐ |

**大宽表/数据仓库场景检查清单** 请参考 [银行财务数字分身 MVP 实施方案](mvp_finance_digital_twin.md) 第 8.8 节。

---

## 附录B：自动化验证脚本

以下 Python 脚本可自动检查工具描述是否符合规范：

```python
"""
MCP 工具描述验证器

用法：
    python tool_description_validator.py <tool_file.py>
"""

import re
import ast
import sys
from dataclasses import dataclass
from typing import Optional


@dataclass
class ValidationResult:
    """验证结果"""
    passed: bool
    issues: list[str]
    warnings: list[str]


class ToolDescriptionValidator:
    """工具描述自动检查工具"""

    # 必需的描述部分
    REQUIRED_PATTERNS = {
        "触发场景": r"当.*询问|调用此工具|时调用",
        "返回内容": r"返回|返回内容|返回字段",
    }

    # 可选但推荐的描述部分
    RECOMMENDED_PATTERNS = {
        "权限约束": r"权限|admin|仅限|管理员",
        "参数约束": r"不接受|参数|身份从认证",
    }

    # 最小描述长度
    MIN_DESCRIPTION_LENGTH = 50

    @staticmethod
    def validate(tool_docstring: str, tool_name: str = "unknown") -> ValidationResult:
        """
        验证工具描述是否符合规范

        Args:
            tool_docstring: 工具的 docstring
            tool_name: 工具名称

        Returns:
            ValidationResult: 验证结果
        """
        issues = []
        warnings = []

        # 1. 检查描述长度
        if len(tool_docstring) < MIN_DESCRIPTION_LENGTH:
            issues.append(
                f"描述过于简洁（{len(tool_docstring)} 字），"
                f"建议至少 {MIN_DESCRIPTION_LENGTH} 字"
            )

        # 2. 检查必需部分
        for section, pattern in REQUIRED_PATTERNS.items():
            if not re.search(pattern, tool_docstring):
                issues.append(f"缺少 '{section}' 说明")

        # 3. 检查推荐部分（仅警告）
        for section, pattern in RECOMMENDED_PATTERNS.items():
            if not re.search(pattern, tool_docstring):
                warnings.append(f"建议添加 '{section}' 说明")

        # 4. 检查命名规范
        if not ToolDescriptionValidator._check_naming(tool_name):
            issues.append(
                f"工具名称 '{tool_name}' 不符合命名规范，"
                f"建议使用 get_/list_/query_/describe_ 等前缀"
            )

        # 5. 检查管理员工具标记
        if "admin" in tool_name.lower() or "all" in tool_name.lower():
            if "【管理员权限工具】" not in tool_docstring:
                warnings.append("管理员工具建议添加 '【管理员权限工具】' 标记")

        return ValidationResult(
            passed=len(issues) == 0,
            issues=issues,
            warnings=warnings
        )

    @staticmethod
    def _check_naming(tool_name: str) -> bool:
        """检查命名是否符合规范"""
        valid_prefixes = [
            "get_", "list_", "query_", "search_",  # 查询类
            "create_", "update_", "delete_",        # 修改类
            "describe_", "check_", "validate_"      # 元数据类
        ]
        return any(tool_name.startswith(prefix) for prefix in valid_prefixes)


def validate_file(file_path: str) -> dict:
    """
    验证 Python 文件中的所有工具描述

    Args:
        file_path: Python 文件路径

    Returns:
        验证结果汇总
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 解析 AST
    tree = ast.parse(content)

    results = {
        "total": 0,
        "passed": 0,
        "failed": 0,
        "tools": []
    }

    # 查找所有 @mcp.tool() 装饰的函数
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            # 检查是否有 @mcp.tool() 装饰器
            has_mcp_tool = False
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call):
                    if isinstance(decorator.func, ast.Attribute):
                        if decorator.func.attr == "tool":
                            has_mcp_tool = True
                            break

            if has_mcp_tool:
                results["total"] += 1
                docstring = ast.get_docstring(node) or ""
                validation = ToolDescriptionValidator.validate(docstring, node.name)

                tool_result = {
                    "name": node.name,
                    "passed": validation.passed,
                    "issues": validation.issues,
                    "warnings": validation.warnings
                }

                results["tools"].append(tool_result)

                if validation.passed:
                    results["passed"] += 1
                else:
                    results["failed"] += 1

    return results


def main():
    """主入口"""
    if len(sys.argv) < 2:
        print("用法: python tool_description_validator.py <tool_file.py>")
        sys.exit(1)

    file_path = sys.argv[1]
    results = validate_file(file_path)

    print(f"\n{'='*60}")
    print(f"MCP 工具描述验证报告")
    print(f"{'='*60}")
    print(f"文件: {file_path}")
    print(f"总计: {results['total']} 个工具")
    print(f"通过: {results['passed']} 个")
    print(f"失败: {results['failed']} 个")
    print(f"{'='*60}\n")

    for tool in results["tools"]:
        status = "✅ 通过" if tool["passed"] else "❌ 失败"
        print(f"{status} - {tool['name']}")

        if tool["issues"]:
            for issue in tool["issues"]:
                print(f"    ❌ {issue}")

        if tool["warnings"]:
            for warning in tool["warnings"]:
                print(f"    ⚠️  {warning}")

        print()

    if results["failed"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
```

### 使用方法

```bash
# 验证单个文件
python tool_description_validator.py prototype/mcp_remote/main.py

# 输出示例
============================================================
MCP 工具描述验证报告
============================================================
文件: prototype/mcp_remote/main.py
总计: 5 个工具
通过: 4 个
失败: 1 个
============================================================

✅ 通过 - get_my_info
✅ 通过 - get_my_balance
❌ 失败 - get_my_department
    ❌ 缺少 '参数约束' 说明
    ⚠️  建议添加 '权限约束' 说明
✅ 通过 - check_my_permission
✅ 通过 - list_all_users
```
