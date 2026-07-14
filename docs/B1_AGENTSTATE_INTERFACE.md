# B1 AgentState Interface

## Responsibility Boundary

B1 owns conversation messages, user-turn boundaries, prompt state, history
compaction, batch orchestration, `AgentState`, checkpoint/resume, replan, degrade,
and cross-module trace data. B3 owns Schema, argument validation, tool execution,
deterministic cache, idempotent retry, and tool metrics. B1 calls B4 and B5 only
through their public interfaces and never invokes a B2 Skill directly.

## Multi-Turn Runtime

Existing single-`user_input` inputs remain valid and normalize to one turn. New
inputs may instead provide:

```json
{
  "conversation_turns": [
    {"turn_id": 1, "content": "First request"},
    {
      "turn_id": 2,
      "content": "Follow-up request",
      "prompt_action": {"operation": "append", "variant": "json_translation"}
    }
  ],
  "max_turns": 4,
  "max_total_llm_calls": 16,
  "max_total_tool_calls": 24,
  "on_user_turn_error": "stop"
}
```

`user_input` and `conversation_turns` are mutually exclusive. Turn IDs must be
positive, unique, and strictly increasing. `max_turns` applies per user turn;
the total limits protect the complete conversation. Error policy is `stop`,
`continue`, or `degrade_continue`.

## Prompt and History

Prompt operations are `keep`, `append`, and `replace`. Replace changes the task
prompt but retains memory context. `prompt_state` and `prompt_history` record the
version, source, effective turn, and SHA256 fingerprints.

Optional history compaction uses:

```json
{
  "history_compaction": {
    "enabled": true,
    "trigger_chars": 12000,
    "target_chars": 4000,
    "preserve_recent_turns": 2,
    "mode": "llm",
    "fallback": "deterministic"
  }
}
```

The full protocol transcript is never deleted. B1 sends B4 a model view with a
summary system message and recent turns. LLM mode uses B4's public API;
deterministic fallback includes query-relevant source extracts. Trace records
the source, errors, covered messages, and before/after character counts.

## B3 Tool Batch Contract

One AIMessage produces exactly one B3 call:

```python
execute_tool_calls(tool_calls, tools_config, toolset, outdir,
    conversation_id=conversation_id,
    execution_context=context,
    schema_mode="manual",
    enable_cache=True,
    enable_retry=True,
    max_attempts=2,
    batch_id=batch_id)
```

`batch_id` is stable for the conversation, LLM call index, and normalized call
list. B1 reads `context.get_batch_report(batch_id)` and records one batch module
call. B1 does not retry individual ToolMessages.

Tool execution values take precedence over an explicit legacy retry mapping and
then `tools.yaml`. Ablation flags are hard gates. B3
`ToolExecutionContext.metrics` remains the sole raw source for cache, retry, and
latency counters; AgentState stores derived snapshots only.

## AgentState

Advanced fields are JSON-serializable:

- `current_user_turn_index`, `current_user_turn_id`, `user_turn_results`;
- `completed_user_turn_ids`, `total_llm_calls`, `total_tool_calls`;
- `active_plan`, `step_progress`, `current_step_id`, `route_history`;
- `prompt_state`, `prompt_history`, `history_compaction`;
- `full_message_count`, `model_message_count`;
- `tool_execution_summary`, `tool_batch_state`, `context_restored`.

B4 AIMessage `plan` and routing metadata are the plan/route facts. Missing or
malformed optional metadata produces a warning; B1 does not instantiate B4
internal planning classes.

## Checkpoint v3

Format 3 uses temporary-file write, flush, fsync, and atomic replace. It stores
user-turn progress, prompt/history state, complete messages, model-view metadata,
runtime fingerprints, and the versioned B3 context snapshot. Changed turns,
base prompt, prompt-action files, declared assets, or identity fields fail before
B4/B3 execution.

Tool batches retain `pending`, `running`, and `completed` recovery semantics.
Non-read-only uncertain running calls are not replayed. A checkpoint taken after
`USER_TURN_COMPLETED` advances without repeating that turn. Format 1/2 uses a
documented migration path with a warning; format 1 without B3 state restores an
empty context.

## Batch and Benchmark

`code/b1_batch_runner.py` executes tasks sequentially with isolated input,
conversation, AgentState, B3 context, and output directories. Duplicate task
IDs, conversation IDs, or output paths fail before execution.
`continue_on_error` controls isolation and produces `batch_report.json` and
`batch_log.jsonl`.

`code/b1_advanced_runner.py` adapts frozen LoCoMo, HotpotQA, MGSM, and Tatoeba
cases. Only turns, prompt operations, and asset paths/fingerprints enter B1.
`expected` and `scoring` are read only after execution. Synthetic fixtures map
to dedicated fault-injection tests rather than natural-language runs.

`code/b1_ablation_runner.py` repeats the same selected cases with controlled
profiles: `enhanced`, `stateless`, `no_compaction`, `no_checkpoint`,
`static_prompt`, and `no_replan_degrade`. It reports score/pass-rate deltas plus
model characters, LLM calls, tool rounds, cache hits, retries, and elapsed time.

## Outputs

Existing `messages.json`, `tool_messages.json`, `final_answer.md`, and
`trace.json` remain. `messages_full.jsonl` stores the full transcript. B3 batch
artifacts remain under `tool_batches/<batch_id>/`. Agent state and checkpoints
are emitted when their switches are enabled.

Fixture mode demonstrates B1 state, round control, failure policy, and recovery.
It is not evidence that B2-B5 are independently demonstrable.
