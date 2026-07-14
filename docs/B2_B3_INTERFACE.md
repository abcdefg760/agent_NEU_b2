# B2/B3 Interface Guide

## Stable Contracts

`SkillResult` keeps the existing top-level keys:

```text
skill_name, status, input, output, error, latency_ms
```

Failed results retain `error.type` and `error.message` and add `code`, `category`,
`retryable`, and optional `details`. `ToolMessage` still contains
`role/tool_call_id/name/content/status`; `content` is the compact JSON encoding of
one `SkillResult`.

Existing calls remain valid:

```python
get_tools_schema(tools_config, toolset, outdir)
execute_tool_calls(tool_calls, tools_config, toolset, outdir)
```

Enhanced calls use optional keywords:

```python
from b3_tool_layer import ToolExecutionContext, execute_tool_calls

context = ToolExecutionContext("conv_001", cache_max_entries=128, cache_ttl_seconds=300)
messages = execute_tool_calls(
    calls,
    tools_config,
    "basic_tools",
    outdir,
    conversation_id="conv_001",
    execution_context=context,
    schema_mode="auto",
    enable_cache=True,
    enable_retry=True,
    max_attempts=2,
    batch_id="batch_001",
)
report = context.get_batch_report("batch_001")
```

Reuse the same context across calls to obtain conversation-level cache hits. B1
now passes one complete AIMessage tool batch, a stable batch ID, and the same
conversation context across rounds. Existing callers may omit every new keyword.

## Toolsets and Skills

- `basic_tools`: the original calculator, file reader, local search, table analyzer,
  and format converter.
- `enhanced_local_tools`: basic tools plus `list_directory` and
  `read_and_convert`.
- `sandbox_tools`: calculator plus the high-risk `python_executor`.
- `network_tools`: the high-risk `web_fetcher` only.
- `all_tools`: explicit demonstration set; it is not the default.

`file_reader` supports bounded TXT, Markdown, JSON, CSV, and TSV input.
`local_file_search` supports `keyword`, `semantic`, and `hybrid`. Semantic modes
load only `settings.semantic_search.model_path`; a missing directory returns
`SEMANTIC_MODEL_UNAVAILABLE` and never triggers a download.

## Schema and Validation

`schema_mode=manual` remains the configured baseline. `schema_mode=auto` inspects
function signatures, type annotations, defaults, and Google-style docstrings.
Framework parameters such as `data_root`, `output_dir`, model paths, allowlists,
and resource limits are hidden. Registration fails on name/import/callable,
annotation, or documentation defects. Schema export writes:

- `tools_schema.json`
- `tool_schema_report.json`
- `schema_diff_report.json`

Validation covers required and unknown fields, nullability, scalar types, arrays
and item types, enums, ranges, lengths, and finite numbers. Each tool call yields
one result even when another call fails.

## Cache, Retry, and Metrics

The cache key is `conversation_id | tool_name | tool_version | canonical_args_sha256`.
Only successful tools marked `deterministic`, `read_only`, and `cacheable` are
stored. Retry applies only when the tool is idempotent, its policy enables retry,
and the execution error reports `retryable=true`. Validation and security failures
never retry. Execution writes `tool_call_log.jsonl` and `tool_metrics.json` with
cache, attempt, success/failure, p50, and p95 data.

## Context Snapshot and Batch Reports

`ToolExecutionContext.snapshot()` produces an integrity-checked, versioned JSON
object containing context identity, restorable metrics, completed batch metadata,
and size-limited cache values. `ToolExecutionContext.restore()` rejects corrupt,
unknown, or identity-mismatched state atomically. TTL uses remaining duration and
offline elapsed time; monotonic process-local timestamps are never serialized.

`get_batch_report(batch_id)` returns one metadata record per input index, including
status, cache hit, attempts, retry count, tool version, and latency. Duplicate IDs
within a batch return `DUPLICATE_TOOL_CALL_ID`; missing IDs are stable for the
batch and input index. Reports are instance state protected by a lock, not a
process-global "last call" value.

## Security Policy

`python_executor` uses an isolated child process, AST/import allowlists, restricted
builtins, a temporary working directory, a cleaned environment, timeout/output
limits, and Linux CPU/address-space/file/process limits. File, network, process,
private attribute, and dynamic execution capabilities are denied by default.

`web_fetcher` accepts only configured HTTP(S) domains, rejects credential URLs and
non-public IPs, validates DNS before each hop and the connected peer, handles
redirects manually, ignores proxy environment variables, and bounds timeout,
response size, decoded text, and Content-Type.
