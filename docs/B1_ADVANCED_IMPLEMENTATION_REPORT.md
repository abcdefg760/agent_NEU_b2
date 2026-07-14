# B1 Advanced Implementation Report

## Scope and Conclusion

This implementation changes B1 and its direct runners, tests, examples, and
documentation. B2-B5 core files and server configuration were not modified.

**Conclusion: partially passed.** The B1 runtime contracts, state transitions,
checkpoint recovery, full/model message separation, batch isolation, benchmark
adapter, and ablation runner pass server tests. Real model execution is
reproducible, but public-task accuracy is below the planned acceptance threshold.
The full development set and 44-case acceptance split remain **unverified**.

## Implemented Capabilities

| Capability | Status | Evidence |
|---|---|---|
| Legacy single-turn compatibility | Passed | Existing B1/B3 checkpoint and batch tests pass |
| Multi-user-turn conversation | Passed | Shared messages/context; per-turn results and limits |
| Dynamic prompt keep/append/replace | Passed structurally | Version/SHA history; real two-turn demo executed |
| B4 plan/route observation | Passed | AgentState records B4 `plan` and route metadata |
| History compaction | Partial | Full transcript retained; LLM/fallback paths work; one real answer remained wrong |
| Checkpoint v3 | Passed | Atomic write, identity checks, completed-turn resume without repetition |
| Batch runner | Passed | Two fixture tasks completed independently, 2/2 |
| Frozen benchmark adapter/scorer | Passed structurally | 127 cases indexed; gold excluded from runtime input |
| Controlled ablation runner | Passed | Six profiles produced comparable metrics |

## Main Changes

- `code/b1_agent_runtime.py`: multi-turn execution, prompt state, B4 plan/route
  synchronization, full/model history views, query-relevant compaction,
  checkpoint v3, total budgets, and ablation switches.
- `code/b1_batch_runner.py`: sequential isolated tasks, duplicate rejection,
  continue/stop policy, `batch_report.json`, and `batch_log.jsonl`.
- `code/b1_advanced_runner.py`: LoCoMo/HotpotQA/MGSM/Tatoeba adaptation,
  asset fingerprints, post-run scoring, and runtime metrics.
- `code/b1_ablation_runner.py`: enhanced/stateless/no-compaction/no-checkpoint/
  static-prompt/no-replan-degrade comparisons.
- `code/run_full_demo.py`: user-turn, prompt, and full/model context metrics.
- Tests: `test_b1_multiturn_prompt.py`, `test_b1_compaction_checkpoint_v3.py`,
  `test_b1_batch_runner.py`, `test_b1_advanced_benchmark.py`, and
  `test_b1_ablation_runner.py`.

## Server Verification

All commands used `/opt/conda/envs/agent/bin/python` in
`/root/siton-tmp/assignment_B/agent`. No dependencies or models were installed.

```bash
/opt/conda/envs/agent/bin/python -m unittest discover -s tests -p 'test_*.py' -v
/opt/conda/envs/agent/bin/python code/b1_batch_runner.py --input data/b1_examples/batch_fixture_input.json --outdir outputs/B1_advanced/stage4_batch_fixture
/opt/conda/envs/agent/bin/python code/b1_ablation_runner.py --benchmark_root data/b1_advanced --outdir outputs/B1_advanced/stage4_ablation_tatoeba_prompt_0004 --tools_config configs/tools.yaml --memory_config configs/memory.yaml --model_config configs/model.yaml --system_prompt prompts/local_tool_agent.txt --llm_mode prompt_json --case_id tatoeba_prompt_0004 --compaction_mode deterministic
/opt/conda/envs/agent/bin/python code/run_full_demo.py --input data/b1_examples/multiturn_prompt_input.json --tools_config configs/tools.yaml --memory_config configs/memory.yaml --model_config configs/model.yaml --llm_mode prompt_json --outdir outputs/B1_advanced/stage4_full_demo
```

Results:

- Full unittest: **75/75 passed** in 7.223 seconds.
- Batch fixture: **2/2 succeeded**, no skipped or failed task.
- Full demo: status `success`, 2/2 user turns, prompt version 2.
- Real ablation: dynamic prompt mean score 0.428571 versus static prompt
  0.228571, a +0.2 relative improvement. Neither reached threshold 0.7.

Logs are under
`/root/siton-tmp/assignment_B/audits/b1_advanced_implementation_20260713_084723/`.
Primary artifacts are under
`/root/siton-tmp/assignment_B/agent/outputs/B1_advanced/`.

## Real Benchmark Findings

The four-dataset smoke run executed four independent public cases. Only 1/4 met
its case threshold:

| Case | Result |
|---|---|
| `mgsm_zh_calc_0001` | Failed: B4 used calculator for remaining weight but omitted the final division |
| `tatoeba_lang_0003` | Passed at dataset threshold 0.5 |
| `hotpotqa_bridge_0001` | Failed: returned Lincoln City instead of Gainsborough Trinity |
| `locomo_cross_0002` | Failed scoring; later asset-grounding fix removed the path rejection |

In the real LoCoMo compaction run, compressed evidence retained “twice a week”
and B4 reasoning metadata concluded the correct fact, while the formal AIMessage
`content` contradicted that reasoning. B1 correctly kept `content` as the public
B4 contract instead of silently replacing it from metadata. This is recorded as
a B4 output-consistency blocker, not a B1 pass.

The dynamic JSON full demo also omitted the requested `source_text` field. The
flow and prompt switch passed; strict format compliance did not.

## Unverified and Blocked

- Development targets (MGSM 18, Tatoeba 18, HotpotQA 14, LoCoMo 14): not run in
  full after the smoke gate showed low correctness.
- Acceptance split (44 public cases): not run.
- Planned 90% fact-retention aggregate and three-run latency medians: not run.
- Poster-level end-to-end accuracy improvement: not established by current
  results; only the single-case dynamic/static prompt delta is established.

## Recovery and Backups

Server backups were created before each deployment:

- `/root/siton-tmp/assignment_B_backups/b1_stage1_20260713_090534`
- `/root/siton-tmp/assignment_B_backups/b1_stage2_20260713_092411`
- `/root/siton-tmp/assignment_B_backups/b1_stage3_20260713_100433`
- `/root/siton-tmp/assignment_B_backups/b1_stage4_20260713_110730`

The final local and server B1-stage files were SHA256-matched after upload.
