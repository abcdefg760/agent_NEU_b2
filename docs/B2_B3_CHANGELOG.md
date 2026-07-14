# B2/B3 Implementation and Verification Notes

## 2026-07-12 B3 Context Completion

- Added versioned context snapshot/restore with integrity, size, TTL, and LRU rules.
- Added strict conversation/toolset/schema/config identity binding.
- Added explicit `batch_id` and `get_batch_report(batch_id)` without global state.
- Added duplicate/missing call ID handling and one report row per input index.
- Added B1 conversation-level context integration and checkpoint restoration.
- Added tests for corrupt/unknown snapshots, normalization, version invalidation,
  restored metrics, pending/running batches, and new-ID cache rebinding.

The retry tests continue to use mock only for recoverable fault injection. Cache,
Schema, batch execution, checkpoint restore, and calculator ablation use real B3
execution. Semantic/hybrid acceptance uses the server-local Qwen3 model separately.

## Implemented Scope

- Added stable error codes and Google-style contracts to all public Skills.
- Extended bounded file reading and implemented keyword/semantic/hybrid search.
- Added directory listing, controlled Python, controlled Web, and composite
  read-and-convert Skills without adding high-risk tools to `basic_tools`.
- Added manual/auto Schema modes, registration checks, recursive argument
  validation, Schema diff, independent multi-call execution, LRU+TTL cache,
  bounded retry, and metrics.
- Added server-side unit tests, normal/error JSON examples, a 36-query retrieval
  benchmark, safety cases, and `code/b2_b3_evaluate.py` for ablations.

No B1 or B4 core logic was modified. `SkillResult`, `ToolMessage`,
`get_tools_schema()`, and `execute_tool_calls()` retain their previous calling and
return structures.

## Differences From the Plan

The plan selected `paraphrase-multilingual-MiniLM-L12-v2`, but the server has
`sentence-transformers 5.6.0` without a cached embedding model. The implementation
therefore enforces offline local-path loading. Keyword retrieval is testable now;
semantic/hybrid quality is marked **unverified** until that exact model is placed
at the configured server path by an authorized operator.

The plan deferred conversation cache state to a future B1 change. This revision
implements `ToolExecutionContext` entirely inside B3 as an optional interface.
Existing B1 behavior is unchanged; cross-round reuse remains a later integration
task.

Python isolation uses process, language-policy, and Linux resource controls
available in the current environment. It is suitable for the bounded course demo,
but it is not a replacement for a separate OS container or seccomp sandbox against
arbitrary hostile code.

## Server Commands

All verification must use the course environment:

```bash
cd /root/siton-tmp/assignment_B/agent
/opt/conda/envs/agent/bin/python -m unittest discover -s tests -p 'test_b*.py' -v
/opt/conda/envs/agent/bin/python code/b2_b3_evaluate.py \
  --dataset data/benchmarks/b2_b3_v1 \
  --tools_config configs/tools.yaml \
  --outdir outputs/ablation/B2_B3/<run_id>
```

The evaluator writes `ablation_report.json`, `ablation_summary.csv`, per-query
retrieval results, manual/auto schemas and diff reports, execution/safety reports,
tool logs, and cache/retry metrics. A retry result is based only on explicit
recoverable fault injection; mock output is not used as general acceptance.

## 2026-07-10 Verification Result

The final server run used `/opt/conda/envs/agent/bin/python` (Python 3.10.20).
All 34 focused B2/B3 unit tests passed. The successful ablation evidence is under
`outputs/ablation/B2_B3/server_validation_20260710_v2/` and contains 137 files.

- Tool execution: 15/15 evaluated cases passed; 9 cases were assigned to dedicated
  cache/retry experiments or skipped because semantic inference is unavailable.
- Safety: 21/21 locally executable cases passed; 3 external HTTP fixture cases were
  skipped.
- Manual versus auto Schema: 9/9 tools compatible, with 0 structural differences.
- Cache: 20 repeated calls required 20 executions when off and 1 execution plus 19
  hits when on (`cache_hit_rate=0.95`).
- Retry fault injection: retry off stopped after one transient failure; retry on
  recovered on attempt 2.
- Keyword retrieval baseline: Recall@5 `0.388889`, MRR `0.368056` on 36 queries.
  This is a baseline measurement, not evidence of semantic improvement.

The first evaluator run is intentionally retained on the server. It exposed a
stable-code mismatch for maximum-value validation (`ARGUMENT_RANGE_ERROR` versus
`INPUT_LIMIT_EXCEEDED`); the implementation was corrected and the v2 run passed.

## Remaining Verification

- **Unverified:** semantic/hybrid inference and retrieval improvement, because the
  configured embedding model directory is absent.
- **Verified:** the server completed a real allowlisted `https://example.com`
  request with HTTP 200, peer verification, `text/html` validation, and a bounded
  559-byte body. Evidence is in `outputs/B2_web_real_20260710/`.
- **Unverified:** a controlled public-to-private redirect fixture is not available;
  direct private/loopback/link-local URLs and redirect targets use the same URL and
  address validator, and the local rejection paths are tested.
- **Not integrated this round:** B1 persistence of `ToolExecutionContext` across
  agent turns and real B4 tool-selection accuracy with the local LLM.
