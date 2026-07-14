# B1 Memory Injection Implementation Report

## 1. Problem and Scope

The original `run_full_demo.py` could complete `file_reader` successfully but still answer with facts copied from valid yet stale historical memory. The defect was in B1 context assembly: the same memory block was injected before planning and again after tool execution, so the model saw conflicting history and current tool evidence without an explicit precedence rule.

This change only covers B1, the full-demo evaluator, related tests, and demo inputs. B2, B4, B5, shared tool configuration, model configuration, and memory storage logic were not modified.

## 2. Implemented Changes

- Added `memory_injection_mode`: `legacy`, `off`, or `phase_aware`.
- In `phase_aware`, memory remains available during planning. After current-turn tool results exist, the copied model context excludes memory and declares ToolMessage evidence authoritative.
- Kept persisted messages and checkpoints unchanged; filtering only affects the per-call deep copy sent to B4.
- Added `memory_injection_decisions.jsonl` with phase, loaded memory IDs, tool call IDs, prompt hash, suppression reason, and formatting-reminder state.
- Added a post-tool formatting reminder derived from the original user request without changing the final ToolMessage role.
- Extended `run_full_demo.py` with answer-level acceptance checks and `demo_evaluation.json`.
- Added six focused tests across `test_b1_memory_injection.py` and `test_run_full_demo_evaluation.py`.

## 3. Server Validation

Required interpreter: `/opt/conda/envs/agent/bin/python` with `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`.

Isolated validation directory:

`/root/siton-tmp/assignment_B/validation/b1_memory_injection_20260713_143949`

Pre-deployment backup:

`/root/siton-tmp/assignment_B_backups/agent_pre_b1_memory_20260713_143949`

Results:

| Check | Result | Evidence |
| --- | --- | --- |
| Focused unit tests | PASS, 6/6 | active `targeted_tests.log` |
| Full regression | PASS, 81/81 | active `full_unittest.log` |
| Real model, `legacy` | FAIL as expected; stale answer reproduced | isolated `real_20260713_144246/legacy` |
| Real model, `off` | PASS | isolated `real_round3_20260713_145319/off` |
| Real model, `phase_aware` | PASS | isolated `real_round3_20260713_145211/phase_aware` |
| Active `phase_aware` demo | PASS | active `phase_aware/demo_evaluation.json` |

Active evidence root:

`/root/siton-tmp/assignment_B/agent/outputs/B1_memory_injection/active_20260713_resume`

The active answer contains three numbered Chinese points and all required fact groups. Both `mem_course_001` and `mem_conversation_conv_000` were loaded. The planning call recorded `memory_injected=true`; the successful post-tool call recorded `memory_injected=false` and `successful_tool_result_is_authoritative`.

## 4. Commands and Interrupted Work

Validation used `py_compile`, focused `unittest`, full `unittest discover`, and three real `run_full_demo.py --llm_mode prompt_json` comparisons. No tests were run locally.

The apparently interrupted final `off` run had already completed with return code 0. Two later SSH commands failed during shell parsing before deployment or model execution; they made no partial changes and were replaced by absolute-path deployment and a reviewed Bash verification script. Server `rsync` was unavailable during isolation setup, so the already-created backup was retained and the validation copy used a tar stream instead; no package or environment was changed.

## 5. Deployment Integrity

Active deployed SHA256 values:

- `code/b1_agent_runtime.py`: `91cb01cfcdc865ed681df6d3a00ee36a5f55c0dc7173276c57dcdee916b7e8df`
- `code/run_full_demo.py`: `aca82978b5cda9bd00d8e993cf3aeacf8f344b4f8a38f60a0bc093580955e9ca`

Collaborator files remained unchanged:

- `code/b4_local_agent_llm.py`: `53f942d3ddb3b64570545ae9f6ec931824609140b80e7b03bd80063e9d99f6bc`
- `code/b5_memory.py`: `7ca077c2fd7552b1aa511a900379130da9fc397015f88634bdef7a21eb8f93c1`

Conclusion: the targeted memory-injection defect is fixed and verified on the active server with the real model. `legacy` remains available as an ablation baseline.
