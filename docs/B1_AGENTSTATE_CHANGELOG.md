# B1 AgentState Change Log

## 2026-07-12 B1/B3 Context Integration

- B1 now submits every AIMessage tool list to B3 as one ordered batch.
- Added optional `tool_execution` and cache/context/retry/replan ablation switches.
- Removed B1 blind tool replay; B3 alone enforces idempotency and retryability.
- Added conversation context reuse, explicit batch reports, and per-batch outputs.
- Added checkpoint format 2 with B3 snapshot, derived metrics, and atomic writes.
- Added pending/running/completed crash recovery and `EXECUTION_STATE_UNCERTAIN`.
- Added server-only regression tests and deterministic B1/B3 ablation evidence.

Compatibility: existing positional B3 calls, ToolMessage/SkillResult shapes,
legacy B1 inputs, and format-1 checkpoints remain supported. Legacy tool retry
configuration now has the restricted B3 execution meaning documented above.

## 本次修改文件
- `code/b1_agent_runtime.py`
- `code/run_full_demo.py`
- `data/b1_fixtures/b1_fixture_agent_state_input.json`
- `data/b1_fixtures/b1_fixture_agent_state_baseline_input.json`
- `data/b1_fixtures/b1_fixture_agent_state_resume_template.json`
- `data/b1_fixtures/b1_fixture_agent_state_recovery_input.json`
- `data/b1_fixtures/preset_ai_messages_recovery.json`
- `data/b1_fixtures/preset_tool_messages_recovery.json`
- `docs/B1_AGENTSTATE_INTERFACE.md`
- `docs/B1_AGENTSTATE_CHANGELOG.md`

## 已实现并可验证
- B1 runtime 增加 `AgentState` dataclass，并默认写出 `agent_state_final.json`。
- `trace.json` 新增 `state_history`、`checkpoints`、`ablation_config`、`module_calls`、`recovery`，旧字段保留。
- 新增可选输入字段：`enable_agent_state`、`enable_checkpoint`、`resume_from_checkpoint`、`retry_policy`、`ablation`。
- B3 工具调用改为按 tool_call 顺序逐个执行，支持同一 AIMessage 多工具调用。
- 支持 B3 工具错误重试、B4 错误重试、失败后 replan 或 degrade。
- 新增 fixture enhanced、baseline、recovery、resume template，用于 B1 独立演示和消融。
- `run_full_demo.py` 报告展示 AgentState 终态和状态迁移数量。

## 兼容性
旧 `data/b1_fixtures/b1_fixture_input.json` 和 `data/runtime_input.json` 不需要新增字段即可运行。默认启用 AgentState，checkpoint 默认关闭，因此旧输出目录只会额外出现 `agent_state_final.json` 与扩展后的 trace 字段。

## Demo Report 影响
full demo 报告新增 `AgentState phase` 与 `State transitions` 两项。其他统计项保持不变。

## Baseline/Enhanced 消融
建议输出目录：

- baseline: `../outputs/ablation/B1/agent_state/baseline/`
- enhanced: `../outputs/ablation/B1/agent_state/enhanced/`

fixture 命令示例：

```bash
python b1_agent_runtime.py --input ../data/b1_fixtures/b1_fixture_agent_state_baseline_input.json --outdir ../outputs/ablation/B1/agent_state/baseline
python b1_agent_runtime.py --input ../data/b1_fixtures/b1_fixture_agent_state_input.json --outdir ../outputs/ablation/B1/agent_state/enhanced
```

## 服务器验证结果
验证时间：2026-07-09。验证路径：`/root/siton-tmp/assignment_B/agent`。可用解释器为 `/opt/conda/envs/agent/bin/python`；系统 `/usr/bin/python3` 缺少 `PyYAML`，不能用于 integrated 验证。

- 旧 fixture：`outputs/B1_fixture_state`，`status=success`，`phase=DONE`。
- AgentState fixture：`outputs/B1_fixture_agent_state`，`status=success`，`phase=DONE`，checkpoint 数量为 5。
- checkpoint resume：`outputs/B1_fixture_agent_state_resumed`，`status=success`，`phase=DONE`。
- recovery fixture：`outputs/B1_fixture_recovery_state`，`status=degraded_done`，`phase=DEGRADED_DONE`。
- mock integrated：`outputs/B1_runtime_state_mock`，`status=success`，`phase=DONE`。
- full demo：`outputs/full_demo_state`，`status=success`，`phase=DONE`，`demo_report.md` 已生成。
- baseline/enhanced 消融：`outputs/ablation/B1/agent_state/baseline` 与 `outputs/ablation/B1/agent_state/enhanced` 已生成；baseline 关闭 AgentState，enhanced 写出 AgentState 和 checkpoint。

本地仍未运行 Python 命令或本地测试。

## 建议服务器验证命令
```bash
cd /root/siton-tmp/assignment_B/agent/code
/opt/conda/envs/agent/bin/python b1_agent_runtime.py --input ../data/b1_fixtures/b1_fixture_input.json --outdir ../outputs/B1_fixture_state
/opt/conda/envs/agent/bin/python b1_agent_runtime.py --input ../data/b1_fixtures/b1_fixture_agent_state_input.json --outdir ../outputs/B1_fixture_agent_state
/opt/conda/envs/agent/bin/python b1_agent_runtime.py --input ../data/b1_fixtures/b1_fixture_agent_state_resume_template.json --outdir ../outputs/B1_fixture_agent_state_resumed
/opt/conda/envs/agent/bin/python b1_agent_runtime.py --input ../data/b1_fixtures/b1_fixture_agent_state_recovery_input.json --outdir ../outputs/B1_fixture_recovery_state
/opt/conda/envs/agent/bin/python b1_agent_runtime.py --input ../data/runtime_input.json --tools_config ../configs/tools.yaml --memory_config ../configs/memory.yaml --model_config ../configs/model.yaml --llm_mode mock --outdir ../outputs/B1_runtime_state_mock
/opt/conda/envs/agent/bin/python run_full_demo.py --input ../data/runtime_input.json --tools_config ../configs/tools.yaml --memory_config ../configs/memory.yaml --model_config ../configs/model.yaml --llm_mode prompt_json --outdir ../outputs/full_demo_state
```

## 已知限制
- integrated resume 保存了 B1 的消息、计数器、memory/schema 快照，但真实 B3/B4/B5 的外部副作用是否完全幂等需要服务器联调确认。
- checkpoint 文件包含完整 `messages` 与 `tools_schema`，仅在显式启用时写出。
- recovery fixture 演示的是 B1 层降级逻辑，不代表真实工具缺失的全部错误形态。
