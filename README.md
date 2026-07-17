
# B2 Skill 实现与增强报告

## 目录

1. [五个基础 Skill 详细说明](#一五个基础-skill)
2. [Skill 与 Tool Schema 的区别](#二skill-与-tool-schema-的区别)
3. [Skill 增强：local_file_search 语义检索](#三skill-增强)
4. [进阶 Skill：沙箱代码执行](#四进阶-skill沙箱代码执行)
5. [复合 Skill：读取后转换](#五复合-skill读取文件后转换)
6. [错误分类体系](#六错误分类体系)
7. [高成本/高风险 Skill 限制](#七高成本高风险-skill-限制)
8. [总结](#八总结)

---

## 一、五个基础 Skill

每个 Skill 均满足：**明确的输入参数、工具功能描述、JSON 可序列化返回值、Google 风格 docstring（Args/Returns/Raises）**。

### 1.1 calculator — 算术表达式计算器

**功能描述**：不依赖 `eval()` 的受限算术表达式求值器，基于 AST 安全解析。

**输入参数**：

| 参数 | 类型 | 必填 | 约束 | 说明 |
|------|------|------|------|------|
| `expression` | `str` | ✅ | minLength=1, maxLength=200 | 算术表达式 |

**支持的运算符**：`+`, `-`, `*`, `/`, `//`, `%`, `**`（指数）、一元 `+`/`-`

**安全限制**：
- 仅解析 AST 节点（`Expression`, `Constant`, `UnaryOp`, `BinOp`），拒绝其他所有节点类型
- 禁止 `eval`/`exec`/`compile` 等动态执行
- 指数绝对值上限 12（防止极大计算量）
- 结果绝对值上限 1e100，拒绝复数/无穷/NaN
- 表达式长度上限 200 字符

**返回值**：

```json
{"result": 14}
```

**错误码**：`INVALID_ARGUMENT`, `INPUT_LIMIT_EXCEEDED`, `INVALID_EXPRESSION`, `DIVISION_BY_ZERO`, `CALCULATION_LIMIT_EXCEEDED`, `UNSUPPORTED_EXPRESSION`

**实现文件**：`skills/calculator.py`（98 行）

---

### 1.2 file_reader — 有界文件读取器

**功能描述**：从数据根目录读取 UTF-8 文本文件，支持 TXT、Markdown、JSON、CSV、TSV 五种格式，JSON/CSV/TSV 返回结构化预览。

**输入参数**：

| 参数 | 类型 | 必填 | 约束 | 说明 |
|------|------|------|------|------|
| `path` | `str` | ✅ | minLength=1 | 相对于 data_root 的文件路径 |
| `max_chars` | `int` | ❌ | 1–100000, default=2000 | 返回的最大字符数 |
| `max_rows` | `int` | ❌ | 1–10000, default=100 | CSV/TSV 最大返回行数 |
| `data_root` | `str` | ❌（框架注入） | — | 框架注入的根目录 |
| `max_file_bytes` | `int` | ❌（框架注入） | >0, default=1000000 | 文件大小上限 |

**返回值**：

```json
{
  "content": "Agent tools provide bounded local capabilities.",
  "num_chars": 45,
  "source": "docs/intro.txt",
  "truncated": false,
  "format": "txt",
  "num_rows": null,
  "truncated_rows": false
}
```

**CSV 结构化预览**（`format: "csv"` 时）：
```json
{
  "content": "{\"columns\": [\"name\", \"value\"], \"rows\": [{\"name\": \"alpha\", \"value\": \"2\"}]}",
  "num_chars": 120,
  "source": "data/table.csv",
  "truncated": true,
  "format": "csv",
  "num_rows": 2,
  "truncated_rows": true
}
```

**路径安全**：检测 Windows 绝对路径绕过（`C:\...`）、`../` 路径遍历、符号链接逃逸

**错误码**：`INVALID_ARGUMENT`, `INPUT_LIMIT_EXCEEDED`, `CONFIGURATION_ERROR`, `UNSUPPORTED_FORMAT`, `FILE_NOT_FOUND`, `FILE_SIZE_LIMIT_EXCEEDED`, `INVALID_TEXT_ENCODING`, `INVALID_JSON`, `INVALID_TABLE`, `PATH_OUTSIDE_DATA_ROOT`

**实现文件**：`skills/file_reader.py`（135 行）

---

### 1.3 local_file_search — 本地文件搜索引擎

**功能描述**：在配置的数据目录下搜索文本文件，支持 **keyword**（关键词匹配）、**semantic**（语义嵌入）、**hybrid**（混合排序）三种模式。

**输入参数**：

| 参数 | 类型 | 必填 | 约束 | 说明 |
|------|------|------|------|------|
| `query` | `str` | ✅ | minLength=1, maxLength=500 | 搜索查询 |
| `root_dir` | `str` | ❌ | minLength=1, default="docs" | 搜索目录 |
| `file_types` | `list[str]` | ❌ | items enum: [txt, md], nullable | 文件类型过滤 |
| `top_k` | `int` | ❌ | 1–100, default=5 | 最大返回数 |
| `mode` | `str` | ❌ | enum: [keyword, semantic, hybrid], default="keyword" | 排序模式 |
| `semantic_weight` | `float` | ❌ | 0–1, default=0.65 | hybrid 模式下语义权重 |
| `data_root` | `str` | ❌（框架注入） | — | 根目录 |
| `embedding_model_path` | `str` | ❌（框架注入） | — | 语义模型路径 |
| `max_files` | `int` | ❌（框架注入） | >0, default=500 | 候选文件上限 |
| `max_file_bytes` | `int` | ❌（框架注入） | >0, default=1000000 | 单文件大小上限 |

**返回值**：

```json
{
  "mode": "hybrid",
  "results": [
    {
      "path": "docs/schema.md",
      "score": 0.85,
      "keyword_score": 0.5,
      "semantic_score": 0.92,
      "normalized_semantic_score": 1.0,
      "keyword_count": 3,
      "snippet": "Tool schema describes arguments..."
    }
  ]
}
```

**三种模式的行为差异**：
- `keyword`：仅按关键词出现次数排序，score = keyword_count
- `semantic`：仅按语义相似度排序，需要本地 SentenceTransformer 模型
- `hybrid`：混合排序，score = (1-semantic_weight) × keyword_normalized + semantic_weight × semantic_normalized

**语义搜索实现细节**：
- 使用 `sentence-transformers` 库
- 仅离线加载本地模型（设置 `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`）
- 优先使用 `encode_query`/`encode_document` 非对称编码（Qwen3-Embedding 系列）
- 回退到对称 `encode`（通用 SentenceTransformer 模型）
- 分数归一化：min-max 到 [0, 1]
- 模型级缓存（进程内单例）

**错误码**：`INVALID_ARGUMENT`, `INPUT_LIMIT_EXCEEDED`, `INVALID_SEARCH_MODE`, `UNSUPPORTED_FORMAT`, `DIRECTORY_NOT_FOUND`, `SEARCH_LIMIT_EXCEEDED`, `SEMANTIC_MODEL_UNAVAILABLE`, `SEMANTIC_DEPENDENCY_UNAVAILABLE`, `SEMANTIC_MODEL_LOAD_FAILED`, `SEMANTIC_INFERENCE_FAILED`, `CONFIGURATION_ERROR`, `PATH_OUTSIDE_DATA_ROOT`

**实现文件**：`skills/local_file_search.py`（291 行）

---

### 1.4 table_analyzer — 表格分析器

**功能描述**：读取 CSV/TSV 文件，输出表格基本信息（行列数、列名、预览行）和数值列统计摘要（计数、最小值、最大值、均值）。

**输入参数**：

| 参数 | 类型 | 必填 | 约束 | 说明 |
|------|------|------|------|------|
| `path` | `str` | ✅ | minLength=1 | 文件路径 |
| `max_rows_preview` | `int` | ❌ | 0–100, default=5 | 预览行数 |
| `describe` | `bool` | ❌ | default=True | 是否计算统计量 |
| `data_root` | `str` | ❌（框架注入） | — | 根目录 |
| `max_file_bytes` | `int` | ❌（框架注入） | >0, default=2000000 | 文件大小上限 |
| `max_rows_limit` | `int` | ❌（框架注入） | >0, default=10000 | 行数上限 |

**返回值**：

```json
{
  "path": "data/scores.csv",
  "num_rows": 3,
  "num_columns": 2,
  "columns": ["name", "score"],
  "preview": [
    {"name": "alpha", "score": "9"},
    {"name": "beta",  "score": "8"}
  ],
  "describe": {
    "score": {
      "count": 3,
      "min": 7.0,
      "max": 9.0,
      "mean": 8.0
    }
  }
}
```

**统计量**（`describe=true` 时）：
- 仅对**全部非空、可转换为 float** 的列计算
- `count`, `min`, `max`, `mean`（使用 `statistics.fmean` 保证浮点精度）

**错误码**：`INVALID_ARGUMENT`, `INPUT_LIMIT_EXCEEDED`, `CONFIGURATION_ERROR`, `UNSUPPORTED_FORMAT`, `FILE_NOT_FOUND`, `FILE_SIZE_LIMIT_EXCEEDED`, `INVALID_TABLE`, `TABLE_ROW_LIMIT_EXCEEDED`, `INVALID_TEXT_ENCODING`, `PATH_OUTSIDE_DATA_ROOT`

**实现文件**：`skills/table_analyzer.py`（124 行）

---

### 1.5 format_converter — 格式转换器

**功能描述**：将输入文本转换为 **Markdown 无序列表** 或 **格式化 JSON**，并写入输出文件。支持 `key: value` 行格式和纯 JSON 两种输入。

**输入参数**：

| 参数 | 类型 | 必填 | 约束 | 说明 |
|------|------|------|------|------|
| `text` | `str` | ✅ | minLength=1, maxLength=100000 | 输入文本 |
| `target_format` | `str` | ✅ | enum: [markdown, json] | 目标格式 |
| `output_filename` | `str` | ❌ | nullable | 输出文件名（仅取 basename） |
| `output_dir` | `str` | ❌（框架注入） | — | 输出目录 |

**转换逻辑**：
- **markdown**：每行文本 → `- <行内容>` 无序列表
- **json**：优先尝试 `json.loads` 解析；失败则按 `key: value` 逐行解析

**返回值**：

```json
{
  "formatted_text": "- item1\n- item2\n- item3",
  "generated_file_path": "/path/to/outputs/format_converter_files/converted.md"
}
```

**文件名安全**：
- 只取 `basename`（拒绝 `../../result.json` 等路径遍历）
- 自动去重（`result(1).json`, `result(2).json`...）
- 不存在则自动创建目录

**错误码**：`INVALID_ARGUMENT`, `INPUT_LIMIT_EXCEEDED`, `UNSUPPORTED_FORMAT`, `CONVERSION_PARSE_ERROR`

**实现文件**：`skills/format_converter.py`（117 行）

---

## 二、Skill 与 Tool Schema 的区别

> **一句话**：Skill 是引擎，Tool Schema 是说明书。

| 维度 | Skill | Tool Schema |
|------|-------|-------------|
| **本质** | 可执行的 Python 函数 | 对函数的 JSON 结构化描述 |
| **位置** | `skills/` 目录下 `.py` 文件 | `configs/tools.yaml` 的 `parameters` 字段，或由代码自动推断 |
| **格式** | Python 代码 | OpenAI Function Calling Schema（JSON Schema） |
| **消费者** | B2 框架直接调用 | **LLM** 用来生成 tool_call，**框架** 用来做参数校验 |
| **职责** | 执行业务逻辑，处理输入，返回输出 | 告诉 LLM：这个工具叫什么、干什么、接受什么参数、返回什么 |

### 2.1 具体例子：calculator

**Skill（`skills/calculator.py`）—— 实际跑的东西**：

```python
from skills.calculator import calculator
result = calculator("2 + 3 * 4")                  # => {"result": 14}
```

**Tool Schema —— LLM 看到的东西**：

```json
{
  "type": "function",
  "function": {
    "name": "calculator",
    "description": "Evaluate a bounded arithmetic expression without using dynamic execution.",
    "parameters": {
      "type": "object",
      "properties": {
        "expression": {
          "type": "string",
          "minLength": 1,
          "maxLength": 200,
          "description": "Arithmetic expression using numbers and supported operators."
        }
      },
      "required": ["expression"],
      "additionalProperties": false
    }
  }
}
```

### 2.2 两种生成方式

| 模式 | 来源 | 优势 |
|------|------|------|
| **manual** | `tools.yaml` 中手写的 `parameters` | 精确控制描述文本，对 LLM 更友好 |
| **auto** | Python 类型注解 + Google docstring 自动推断 | 零维护，与代码强制同步 |

B2 自动生成 manual vs auto 的 **diff 报告**，确保两者一致（当前 9/9 零差异）。

### 2.3 为什么要分开

```
用户输入 "2+3等于几？"
       │
       ▼
  LLM 读 Tool Schema（说明书）
       → 决定调用 calculator(expression="2+3")
       │
       ▼
  框架用 Tool Schema 校验参数格式是否正确
       │
       ▼
  B2 动态加载 Skill（引擎）
       → 执行 calculator("2+3")
       → 返回 {"result": 5}
       │
       ▼
  LLM 收到 {"result": 5}
       → 回复用户 "2+3等于5"
```

- LLM 只看得懂 JSON 描述，看不懂 Python 源码 → 需要 **Tool Schema**
- 框架需要实际执行逻辑 → 需要 **Skill**
- 同一个 Skill 可以有不同的 Schema 描述（比如给不同 LLM 看不同语言的 description），两者解耦

---

## 三、Skill 增强

### 3.1 local_file_search 的本地检索能力增强

原有的 `local_file_search` 仅支持关键词匹配（统计词频 → 排序）。增强后新增两种检索模式：

#### semantic（语义搜索）

基于 `sentence-transformers` 库，将查询和文档分别编码为嵌入向量，计算余弦相似度排序。

```python
# 使用示例
result = local_file_search(
    "machine learning for text",
    root_dir="docs",
    mode="semantic",
    embedding_model_path="/path/to/Qwen3-Embedding-0.6B",
)
# 返回语义上最相关的文档，即使不包含 "machine" 或 "learning" 字样
```

**关键设计点**：
- 仅离线加载（`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`），永不触发网络下载
- 模型目录不存在时返回 `SEMANTIC_MODEL_UNAVAILABLE`（优雅降级，不崩溃）
- 模型级缓存（`_SEMANTIC_MODELS` 字典）避免重复加载
- 环境变量恢复（finally 块还原 HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE）

#### hybrid（混合搜索）

```python
score = (1 - semantic_weight) × keyword_normalized + semantic_weight × semantic_normalized
```

- `semantic_weight=0.0`：退化为纯关键词搜索
- `semantic_weight=1.0`：退化为纯语义搜索
- 默认 `0.65`：语义为主，关键词辅助

#### 非对称编码支持

对于支持 task-specific 编码的模型（如 Qwen3-Embedding 系列），优先使用 `model.encode_query()` / `model.encode_document()`，回退到通用的 `model.encode()`。

#### 分数归一化

`_normalize_scores()` 对语义分数做 min-max 归一化 [0, 1]，保留排序不变，使混合模式的权重系数有意义。

### 3.2 file_reader 格式扩展

| 特性 | 增强前 | 增强后 |
|------|--------|--------|
| 支持格式 | `.txt`, `.md` | `.txt`, `.md`, `.json`, `.csv`, `.tsv` |
| JSON 处理 | ❌ | `json.loads` → `json.dumps(indent=2)` 重新格式化 |
| CSV/TSV 处理 | ❌ | `csv.DictReader` → 结构化行列预览 |
| 文件大小限制 | ❌ | `max_file_bytes` 可配置 |
| 行限制 | ❌ | `max_rows` 控制 CSV/TSV 行数 |
| UTF-8 校验 | ❌ | 显式检测 `UnicodeDecodeError` |

### 3.3 路径安全增强（`skills/__init__.py`）

`resolve_data_path()` 新增：
- **Windows 绝对路径绕过检测**：`C:\Windows\...` 在 POSIX 系统上 `Path.is_absolute()` 为 False，但 `PureWindowsPath.is_absolute()` 为 True
- **空路径检测**：拒绝空字符串或纯空白路径
- **全部使用 SkillError** 替代裸 `ValueError`

---

## 四、进阶 Skill：沙箱代码执行

### 4.1 python_executor

**实现文件**：`skills/python_executor.py`（371 行）

**功能描述**：在隔离的子进程中执行受限的 Python 计算代码。

#### 多层安全防护

**第一层 — AST 静态分析**（`_SafetyValidator`）：

| 禁止项 | 错误码 |
|--------|--------|
| 导入非白名单模块（仅允许 `math`, `statistics`） | `PYTHON_IMPORT_DENIED` |
| `eval`, `exec`, `compile`, `__import__` | `PYTHON_DYNAMIC_EXEC_DENIED` |
| `open`, `input`, `globals`, `locals`, `getattr` 等 | `PYTHON_CAPABILITY_DENIED` |
| 私有属性访问（`_xxx`, `__xxx__`） | `PYTHON_CAPABILITY_DENIED` |
| `class` 定义 | `PYTHON_CAPABILITY_DENIED` |
| `global` / `nonlocal` 声明 | `PYTHON_CAPABILITY_DENIED` |

**第二层 — 子进程隔离**：
- 独立 Python 解释器进程（`subprocess.run`）
- `-I`（隔离模式）+ `-S`（不 import site）启动参数
- 通过 stdin 传递代码，stdout 捕获结果

**第三层 — 受限内置函数**（26 个安全函数）：
```python
{"abs", "all", "any", "bool", "dict", "enumerate", "Exception", "filter",
 "float", "int", "len", "list", "map", "max", "min", "print", "range",
 "reversed", "round", "set", "sorted", "str", "sum", "tuple", "TypeError",
 "ValueError", "zip"}
```
自定义 `__import__` 替代原生 import，仅允许白名单模块。

**第四层 — 清理环境变量**：
```
HOME=/tmp/..., LANG=C.UTF-8, PATH=/usr/local/bin:..., PYTHONHASHSEED=0
```
移除所有用户环境变量、HTTP_PROXY、PYTHONPATH 等。

**第五层 — Linux 资源限制**（`RLIMIT_*`）：

| 资源 | 限制 |
|------|------|
| `RLIMIT_CORE` | 0（不生成 core dump） |
| `RLIMIT_CPU` | timeout+1 秒 |
| `RLIMIT_AS` | `memory_limit_mb` MB 地址空间 |
| `RLIMIT_FSIZE` | `max_output_chars × 4` 字节文件写入 |
| `RLIMIT_NOFILE` | 16 个文件描述符 |
| `RLIMIT_NPROC` | 1 个进程（禁止 fork） |

**第六层 — 输出限制**：
- stdin 传递代码（非命令行参数，防止 shell 注入）
- stdout/stderr/result 均受 `max_output_chars` 约束
- 子进程侧 `LimitedBuffer` 实时检查输出溢出

**第七层 — 超时**：
- `subprocess.run(timeout=...)` 子进程级别超时
- 超时报 `PYTHON_TIMEOUT` 错误

#### 输入参数

| 参数 | 类型 | 必填 | 约束 | 说明 |
|------|------|------|------|------|
| `code` | `str` | ✅ | minLength=1, maxLength=10000 | Python 代码 |
| `timeout_seconds` | `float` | ❌ | 0.1–10, default=2.0 | 超时秒数 |
| `max_output_chars` | `int` | ❌（注入） | 100–100000, default=16000 | 输出字符上限 |
| `memory_limit_mb` | `int` | ❌（注入） | 64–1024, default=256 | 内存上限 |

#### 返回值

```json
{
  "stdout": "",
  "stderr": "",
  "result": [1, 2, 3, 4],
  "latency_ms": 12.5,
  "isolation": {
    "child_process": true,
    "isolated_mode": true,
    "linux_resource_limits": true
  }
}
```

---

## 五、复合 Skill：读取文件后转换

### 5.1 read_and_convert

**实现文件**：`skills/read_and_convert.py`（55 行）

**功能描述**：组合 `file_reader` 和 `format_converter` 两个 Skill，一步完成"读取有界文件 → 转换格式"。

```python
def read_and_convert(
    path: str,
    target_format: Literal["markdown", "json"],
    output_filename: str | None = None,
    max_chars: int = 20_000,
    *,
    data_root: str | None = None,
    output_dir: str | None = None,
    max_file_bytes: int = 1_000_000,
) -> dict[str, Any]:
    read_result = file_reader(path, max_chars=max_chars, data_root=data_root, max_file_bytes=max_file_bytes)
    conversion_result = format_converter(read_result["content"], target_format, output_filename, output_dir)
    return {
        "source": read_result["source"],
        "read": read_result,
        "conversion": conversion_result,
        "formatted_text": conversion_result["formatted_text"],
        "generated_file_path": conversion_result["generated_file_path"],
    }
```

**设计特点**：
- 不复制逻辑，直接调用已有 Skill
- 错误自动传播（任一子 Skill 抛出 SkillError 均向上传递）
- 返回值包含完整的三层数据（source → read → conversion），便于调试
- 统一的参数注入（`data_root`, `output_dir`, `max_file_bytes` 一次注入，透传子 Skill）

**使用示例**：
```python
result = read_and_convert(
    "docs/intro.txt",
    "markdown",
    "intro-converted.md",
    data_root="/data",
    output_dir="/outputs",
)
# result["formatted_text"] → 转换后的 Markdown
# result["generated_file_path"] → 输出文件路径
# result["read"] → 原始 file_reader 结果（含 truncated 等信息）
```

---

## 六、错误分类体系

### 6.1 SkillError 结构

所有 Skill 统一使用 `skills/errors.py` 中定义的 `SkillError`：

```python
class SkillError(Exception):
    def __init__(
        self,
        code: str,        # 稳定机器可读错误码
        message: str,      # 人类可读描述
        *,
        category: str = "execution",  # 错误分类
        retryable: bool = False,      # 是否可重试
        details: dict | None = None,  # 附加调试信息
    ) -> None: ...
```

### 6.2 错误分类（category）

| 分类 | 含义 | 示例错误码 |
|------|------|-----------|
| `validation` | 输入参数不符合要求 | `INVALID_ARGUMENT`, `DIVISION_BY_ZERO`, `UNSUPPORTED_FORMAT`, `INVALID_SEARCH_MODE`, `INVALID_EXPRESSION` |
| `security` | 违反安全策略 | `PATH_OUTSIDE_DATA_ROOT`, `URL_SCHEME_DENIED`, `URL_PRIVATE_ADDRESS`, `URL_CREDENTIALS_DENIED`, `URL_DOMAIN_DENIED`, `PYTHON_IMPORT_DENIED`, `PYTHON_CAPABILITY_DENIED`, `PYTHON_DYNAMIC_EXEC_DENIED`, `WEB_DNS_REBINDING_DETECTED`, `CONTENT_TYPE_DENIED` |
| `not_found` | 资源不存在 | `FILE_NOT_FOUND`, `DIRECTORY_NOT_FOUND` |
| `limit` | 超出配置的资源限制 | `INPUT_LIMIT_EXCEEDED`, `FILE_SIZE_LIMIT_EXCEEDED`, `TABLE_ROW_LIMIT_EXCEEDED`, `SEARCH_LIMIT_EXCEEDED`, `CALCULATION_LIMIT_EXCEEDED`, `WEB_REDIRECT_LIMIT_EXCEEDED`, `RESPONSE_SIZE_LIMIT_EXCEEDED` |
| `execution` | 运行时执行失败 | `SKILL_EXECUTION_ERROR`, `SEMANTIC_INFERENCE_FAILED`, `CONVERSION_PARSE_ERROR`, `PYTHON_RUNTIME_ERROR`, `PYTHON_PROTOCOL_ERROR`, `DIRECTORY_READ_ERROR` |
| `timeout` | 操作超时 | `EXECUTION_TIMEOUT`, `PYTHON_TIMEOUT`, `WEB_TIMEOUT` |
| `network` | 网络相关错误 | `TRANSIENT_NETWORK_ERROR`, `WEB_DNS_ERROR`, `HTTP_RETRYABLE_ERROR`, `HTTP_ERROR`, `WEB_REDIRECT_ERROR` |
| `configuration` | 配置错误 | `CONFIGURATION_ERROR`, `FEATURE_DISABLED`, `TOOL_DISABLED`, `SEMANTIC_MODEL_UNAVAILABLE`, `WEB_POLICY_ERROR` |
| `dependency` | 依赖缺失 | `SEMANTIC_DEPENDENCY_UNAVAILABLE`, `SEMANTIC_MODEL_LOAD_FAILED`, `WEB_DEPENDENCY_UNAVAILABLE` |
| `contract` | 契约违例 | `INVALID_SKILL_OUTPUT` |
| `authorization` | 权限不足 | `TOOL_NOT_ALLOWED`, `PERMISSION_DENIED` |

### 6.3 完整错误码清单（按 Skill 分组）

#### calculator
`INVALID_ARGUMENT`, `INPUT_LIMIT_EXCEEDED`, `INVALID_EXPRESSION`, `UNSUPPORTED_EXPRESSION`, `DIVISION_BY_ZERO`, `CALCULATION_LIMIT_EXCEEDED`

#### file_reader
`INVALID_ARGUMENT`, `INPUT_LIMIT_EXCEEDED`, `CONFIGURATION_ERROR`, `UNSUPPORTED_FORMAT`, `FILE_NOT_FOUND`, `FILE_SIZE_LIMIT_EXCEEDED`, `INVALID_TEXT_ENCODING`, `INVALID_JSON`, `INVALID_TABLE`, `PATH_OUTSIDE_DATA_ROOT`

#### local_file_search
`INVALID_ARGUMENT`, `INPUT_LIMIT_EXCEEDED`, `INVALID_SEARCH_MODE`, `UNSUPPORTED_FORMAT`, `DIRECTORY_NOT_FOUND`, `SEARCH_LIMIT_EXCEEDED`, `CONFIGURATION_ERROR`, `SEMANTIC_MODEL_UNAVAILABLE`, `SEMANTIC_DEPENDENCY_UNAVAILABLE`, `SEMANTIC_MODEL_LOAD_FAILED`, `SEMANTIC_INFERENCE_FAILED`, `PATH_OUTSIDE_DATA_ROOT`

#### table_analyzer
`INVALID_ARGUMENT`, `INPUT_LIMIT_EXCEEDED`, `CONFIGURATION_ERROR`, `UNSUPPORTED_FORMAT`, `FILE_NOT_FOUND`, `FILE_SIZE_LIMIT_EXCEEDED`, `INVALID_TABLE`, `TABLE_ROW_LIMIT_EXCEEDED`, `INVALID_TEXT_ENCODING`, `PATH_OUTSIDE_DATA_ROOT`

#### format_converter
`INVALID_ARGUMENT`, `INPUT_LIMIT_EXCEEDED`, `UNSUPPORTED_FORMAT`, `CONVERSION_PARSE_ERROR`

#### list_directory
`INVALID_ARGUMENT`, `DIRECTORY_NOT_FOUND`, `DIRECTORY_READ_ERROR`, `PATH_OUTSIDE_DATA_ROOT`

#### python_executor
`INVALID_ARGUMENT`, `INPUT_LIMIT_EXCEEDED`, `CONFIGURATION_ERROR`, `PYTHON_SYNTAX_ERROR`, `PYTHON_IMPORT_DENIED`, `PYTHON_DYNAMIC_EXEC_DENIED`, `PYTHON_CAPABILITY_DENIED`, `PYTHON_TIMEOUT`, `PYTHON_RESOURCE_LIMIT`, `PYTHON_PROTOCOL_ERROR`, `PYTHON_RUNTIME_ERROR`, `OUTPUT_LIMIT_EXCEEDED`

#### web_fetcher
`INVALID_ARGUMENT`, `INPUT_LIMIT_EXCEEDED`, `WEB_POLICY_ERROR`, `INVALID_URL`, `URL_SCHEME_DENIED`, `URL_CREDENTIALS_DENIED`, `URL_PRIVATE_ADDRESS`, `URL_DOMAIN_DENIED`, `WEB_DNS_ERROR`, `WEB_DNS_REBINDING_DETECTED`, `WEB_TIMEOUT`, `TRANSIENT_NETWORK_ERROR`, `WEB_PEER_UNVERIFIED`, `WEB_REDIRECT_ERROR`, `WEB_REDIRECT_LIMIT_EXCEEDED`, `HTTP_RETRYABLE_ERROR`, `HTTP_ERROR`, `CONTENT_TYPE_DENIED`, `RESPONSE_SIZE_LIMIT_EXCEEDED`, `WEB_DEPENDENCY_UNAVAILABLE`

### 6.4 标准异常自动映射

`exception_to_error()` 将 Python 标准异常自动转换为结构化错误：

```python
FileNotFoundError  → code="FILE_NOT_FOUND",    category="not_found",    retryable=False
PermissionError    → code="PERMISSION_DENIED",  category="security",     retryable=False
TimeoutError       → code="EXECUTION_TIMEOUT",  category="timeout",      retryable=True
TypeError/ValueError → code="INVALID_ARGUMENT",  category="validation",   retryable=False
其他                → code="SKILL_EXECUTION_ERROR", category="execution", retryable=False
```

---

## 七、高成本/高风险 Skill 限制

### 7.1 限制策略总览

| 限制类型 | 适用 Skill | 具体机制 |
|----------|-----------|----------|
| **输入大小限制** | 全部 | maxLength/max_chars/max_rows |
| **输出大小限制** | python_executor, web_fetcher | max_output_chars, max_chars |
| **文件大小限制** | file_reader, table_analyzer, local_file_search | max_file_bytes |
| **数量限制** | local_file_search, list_directory, table_analyzer | max_files, max_entries, max_rows_limit |
| **时间限制** | python_executor, web_fetcher | timeout_seconds |
| **内存限制** | python_executor | memory_limit_mb + RLIMIT_AS |
| **进程限制** | python_executor | isolated child process |
| **域名限制** | web_fetcher | allowed_domains 白名单 |
| **协议限制** | web_fetcher | 仅 HTTP(S)，可禁止 HTTP |
| **内容类型限制** | web_fetcher | allowed_content_types 白名单 |
| **重定向限制** | web_fetcher | max_redirects，每跳验证 DNS |
| **Feature 开关** | python_executor, web_fetcher | 可全局关闭高危 Skill |

### 7.2 具体限制配置（`configs/tools.yaml`）

```yaml
settings:
  limits:
    max_file_bytes: 2000000      # 文件读取上限 2MB
    max_search_files: 500        # 搜索候选文件上限
    max_table_rows: 10000        # 表格行数上限

  python_executor:
    max_output_chars: 16000      # 输出上限
    memory_limit_mb: 256         # 内存上限

  web_fetcher:
    allowed_domains:             # 域名白名单
      - example.com
    allow_http: false            # 默认禁止 HTTP
    max_response_bytes: 1000000  # 响应体上限 1MB
    allowed_content_types:       # Content-Type 白名单
      - text/plain
      - text/html
      - application/json
    max_redirects: 3             # 重定向上限

  cache:
    max_entries: 128             # 缓存条目数
    ttl_seconds: 300             # 缓存 TTL

  retry:
    max_attempts: 2              # 最大重试次数
    backoff_seconds: 0.05        # 退避基数

  features:
    python_executor: true        # 可全局关闭
    web_fetcher: true            # 可全局关闭
```

### 7.3 风险评估配置（每个 tool 级别）

```yaml
tools:
  python_executor:
    risk: high           # 高风险标记
    deterministic: false
    read_only: false
    idempotent: false
    cacheable: false     # 高风险操作不缓存
    retry: false         # 高风险操作不自动重试
  web_fetcher:
    risk: high
    cacheable: false
    retry: true          # 网络错误可重试（idempotent=true）
```

---

## 八、总结

### 8.1 实现清单

| 需求 | 状态 | 对应文件 |
|------|------|----------|
| 5 个基础 Skill | ✅ | `skills/calculator.py`, `file_reader.py`, `local_file_search.py`, `table_analyzer.py`, `format_converter.py` |
| 明确输入/输出/描述 | ✅ | 每个函数含完整类型注解 + Google docstring |
| JSON 可序列化输出 | ✅ | 全部返回 `dict`，内部仅含 JSON 兼容类型 |
| Skill vs Tool Schema 说明 | ✅ | 第二章完整论述 |
| 增强现有 Skill | ✅ | `local_file_search` 语义/混合搜索、`file_reader` 多格式 |
| 进阶 Skill（沙箱） | ✅ | `python_executor` — 7 层安全防护 |
| 复合 Skill | ✅ | `read_and_convert` — file_reader + format_converter |
| 错误分类 + 错误码 | ✅ | `skills/errors.py` — 10 个 category，60+ 个稳定错误码 |
| 高成本/风险限制 | ✅ | 输入/输出/文件/时间/内存/进程/网络多层限制 |

### 8.2 项目结构

```
agent2/
├── code/
│   ├── b2_run_skill.py          # B2 Skill 执行入口（增强版）
│   └── common/                  # 公共模块
├── skills/
│   ├── __init__.py              # resolve_data_path（增强版）
│   ├── errors.py                # SkillError 结构化错误体系
│   ├── calculator.py            # 5个基础Skill
│   ├── file_reader.py
│   ├── local_file_search.py
│   ├── table_analyzer.py
│   ├── format_converter.py
│   ├── list_directory.py        # 新增
│   ├── python_executor.py       # 新增（沙箱）
│   ├── web_fetcher.py           # 新增（安全Web）
│   └── read_and_convert.py      # 新增（复合）
├── configs/
│   └── tools.yaml               # Skill 配置与限制参数
├── tests/
│   ├── test_b2_contract.py      # 契约测试（6个）
│   └── test_b2_enhanced.py      # 增强测试（11个）
└── docs/
    └── B2_implementation_report.md
```

### 8.3 验证结果

- **17** 个 B2 单元测试全部通过（`python -m unittest discover -s tests -p 'test_b2*.py' -v`）
- **15/15** 工具执行正确性用例通过
- **21/21** 安全策略用例通过
- 所有 Skill 返回 JSON 可序列化输出，含完整错误码与分类
