# B1 记忆注入问题定向修改方案

## 1. 本轮只解决一个失败

服务器上的真实 `run_full_demo` 已经完成了工具调用，但最终答案错误。实际链路是：

```text
用户要求读取 docs/agent_intro.txt 并总结三点
  -> 第一次 B4 调用正确选择 file_reader
  -> B2/B3 返回成功 ToolMessage，文件内容正确
  -> 第二次 B4 调用生成最终答案
  -> 最终答案复制旧会话 memory，而不是总结 ToolMessage
```

因此，失败点不是 B2/B3，也不是 B5 没有返回数据，而是 **B1 在第二次 B4 调用时仍把旧 memory 放在 system prompt 中**。当前旧会话正文约 1900 字符，包含旧 `Final Answer`、`Messages` 和 `Trace`；当前文件工具结果只有约 92 字符。小模型最终选择了 system 中更长、更像现成答案的旧内容。

本轮不建设通用 memory 框架，不修改 B4/B5，不扩展检索、per-turn memory 或 checkpoint。先修复这一个确定的上下文生命周期错误，并用同一份包含冲突旧记忆的输入证明修复有效。

## 2. 修复原则：记忆只参与规划，不覆盖当前工具证据

B1 每次调用 B4 前，根据当前用户轮所处阶段重新生成 **仅供本次模型调用使用的 system prompt**：

| 阶段 | 当前轮状态 | memory 处理 |
|---|---|---|
| `planning` | 尚无 ToolMessage | 允许注入，保持第一次工具选择行为 |
| `tool_success` | 存在成功 ToolMessage | 从本次 B4 输入中移除 memory 正文 |
| `tool_error` | 存在失败 ToolMessage | 移除 memory，要求解释当前工具错误 |
| `direct_answer` | 当前轮不调用工具 | 保留 memory，维持直接记忆问答能力 |

这不是简单关闭 B5：B5 仍被调用，`selected_memory.json` 仍应包含 global memory 和 `mem_conversation_conv_000`。改变的只是第二次 B4 调用时的证据路由。

## 3. 精确代码修改

### 3.1 保留持久消息，生成单次调用视图

不要直接修改 `messages[0]` 或已写入 checkpoint 的消息。调用 B4 前创建 `model_messages = deepcopy(messages)`，只替换副本中的 system 内容。

新增三个小函数：

```python
def _current_turn_tool_state(messages: list[dict], user_turn_id: int) -> dict:
    """返回当前用户轮的成功/失败 ToolMessage 及 tool_call_id。"""

def _memory_injection_phase(tool_state: dict) -> str:
    """返回 planning、tool_success 或 tool_error。"""

def _system_prompt_for_model_call(
    base_task_prompt: str,
    memory_context: str,
    phase: str,
    mode: str,
) -> tuple[str, dict]:
    """返回本次 B4 system prompt 和可审计的注入决策。"""
```

`tool_success` 阶段使用：

```text
The current user turn has a successful ToolMessage.
For facts obtained by that tool, the latest successful ToolMessage is authoritative.
Answer from that ToolMessage, not from retrieved memory or an earlier answer.
Follow the current user's requested language, format, and number of points.
```

该 prompt 后不再附加 `<memory>...</memory>`。工具结果已经作为标准 ToolMessage 留在消息序列中，无需复制到 system。

### 3.2 增加三个可消融模式

运行输入新增一个窄范围字段，不引入上一版的大型 policy：

```json
{
  "memory_injection_mode": "phase_aware"
}
```

- `legacy`：完全保留当前行为，用于复现错误。
- `phase_aware`：第一次调用可见 memory；成功或失败的工具反馈出现后，后续调用不再注入 memory。
- `off`：B5 仍加载并落盘，但所有 B4 调用都不注入，用作因果对照。

未提供该字段时暂时保持 `legacy`，避免影响其他协作者输入；`data/runtime_input.json` 显式改成 `phase_aware`。

### 3.3 记录真正送入 B4 的内容

新增 `memory_injection_decisions.jsonl`，每次 B4 调用记录：

```json
{
  "llm_call": 2,
  "user_turn_id": 1,
  "mode": "phase_aware",
  "phase": "tool_success",
  "memory_loaded_ids": ["mem_course_001", "mem_conversation_conv_000"],
  "memory_injected": false,
  "suppressed_reason": "successful_tool_result_is_authoritative",
  "tool_call_ids": ["call_001"],
  "model_system_prompt_sha256": "..."
}
```

验收时必须检查第二次 B4 原始调用输入，而不能只看 B1 自报字段。

## 4. 不允许通过修改输入掩盖问题

回归输入继续保留：

```json
{
  "selected_memory_ids": ["mem_conversation_conv_000"],
  "use_global_memory": true
}
```

即使该 memory 与当前任务冲突，`phase_aware` 也必须得到正确文件摘要。验收副本仅把 `save_memory` 改为 `none`，防止重复测试继续向 active memory 写入验收内容；不得删除旧 memory ID、关闭 global 或改写旧记忆内容来制造通过。

## 5. 定向测试

### 5.1 结构测试

新增 `tests/test_b1_memory_injection.py`，固定使用当前错误中的旧 memory 和 file_reader ToolMessage：

1. `legacy`：第二次 B4 输入仍含旧 memory，证明基线可复现。
2. `phase_aware`：第一次调用含 memory；第二次调用不含 `<memory>`、旧答案、`Messages` 或 `Trace`。
3. 第二次调用必须保留用户请求、assistant tool call 和成功 ToolMessage。
4. 工具失败时 memory 不得被当作替代答案。
5. 无工具的直接问答仍可使用 memory。
6. `off` 模式两次调用均不注入，但 B5 加载产物仍存在。

Mock 只用于捕获 B4 实际收到的消息，不作为效果验收。

### 5.2 服务器真实模型三组对照

使用同一模型、同一用户输入、同一 memory 和同一工具配置：

| 组别 | 模式 | 预期 |
|---|---|---|
| A | `legacy` | 复现旧记忆覆盖当前工具证据，作为失败基线 |
| B | `off` | 文件摘要正确，证明 B2/B3/B4 无 memory 时可工作 |
| C | `phase_aware` | memory 已加载，但最终仍得到与 B 组一致的正确摘要 |

只有 B、C 都通过且 A 能复现原问题，才能把修复效果归因于注入阶段控制。

## 6. run_full_demo 的结果判定

当前 `run_full_demo.py` 只检查流程 `status=success`，错误答案也会被报告为成功。为演示输入增加独立 expectation 文件，评测内容不进入模型 prompt：

```json
{
  "required_tool": "file_reader",
  "required_point_count": 3,
  "required_fact_groups": [
    ["模型", "工具", "记忆", "执行循环"],
    ["读取本地文件", "执行计算"],
    ["全局知识", "历史对话上下文"]
  ],
  "forbidden_stale_facts": ["自主感知环境", "无需人类持续干预", "机器人、客户服务"]
}
```

`run_full_demo.py` 输出 `demo_evaluation.json`，报告同时显示：

- `pipeline_status`
- `answer_acceptance_status`
- 实际工具调用与状态
- 三组事实覆盖结果
- 禁止旧事实命中结果
- memory 第二次调用是否被抑制

提供 expectation 时，答案验收失败必须返回非零退出码，不能再以 AgentState 到达 `DONE` 代替正确性。

## 7. 实施顺序与停止条件

1. 在服务器新输出目录运行 `legacy`，保存错误答案、第二次 B4 输入和 selected memory。
2. 只实现单次模型视图和 `phase_aware`，不同时修改检索、B4 路由或 B5 数据。
3. 运行结构测试；第二次 B4 输入仍出现 memory 时立即停止修复。
4. 运行真实模型 B、C 组；C 未通过时检查 B4 实际输入，不先增加答案重试或硬编码答案。
5. 若 B 通过而 C 失败，说明 memory 仍从其他消息路径泄漏，继续修复上下文构建。
6. 若 B、C 都失败，问题已不再是 memory 注入，应转交 B4 的最终回答路由处理，禁止在 B1 增加伪正确的模板答案。
7. 三组对照成立后，再增加 `demo_evaluation.json` 并执行最终统一演示。

## 8. 本轮修改范围

- `code/b1_agent_runtime.py`
- `code/run_full_demo.py`
- `data/runtime_input.json` 及两份对照输入
- `data/run_full_demo_expectations.json`
- `tests/test_b1_memory_injection.py`
- B1 接口与测试报告文档

不修改 `code/b4/`、`code/b5_memory.py`、`configs/memory.yaml` 或 active memory 文件。

## 9. 最终通过标准

同一份包含旧 `mem_conversation_conv_000` 的输入在 `phase_aware` 下必须满足：

- 第一次 B4 调用仍正确选择 `file_reader`；
- file_reader 只执行一次并成功返回 `docs/agent_intro.txt`；
- 第二次 B4 实际输入中不存在 memory 正文，但存在完整 ToolMessage；
- 最终输出恰好三条中文要点，并覆盖文件中的三组事实；
- 不出现旧会话中的替代性 Agent 定义；
- `selected_memory.json` 仍证明 B5 已加载旧 memory；
- `demo_evaluation.json` 为 `PASS`，进程退出码为 0。

这才证明解决了“memory 已接入但不能覆盖当前工具事实”的问题，而不是绕过了 B5。
