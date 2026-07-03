# VLM Agent Efficiency Improvement Log

All changes that aim to reduce runtime while preserving accuracy must be logged here with before/after data.

## Baseline Run: 20260602-101045-task (case_011)
- Date: 2026-06-02
- Total time: 371.28s
- Breakdown: part0=139.12s | partB=77.41s | partA=40.70s | partD=93.22s | unknown=20.84s
- LLM call times: 20-35s each (prefill heavy due to large board photos + assembly drawings)
- Final output: pixel [2038,1114], tp_id=TP1 (note: golden uses TP2 but pixel matches)
- Accuracy vs golden: pixel match ✓ (2038,1114)

## Golden Files（6个，位于 training_platform/golden/）
这些是判断“准确性不变”的唯一依据：
1. case10_signal_to_tp.json          — Part0 输出的 TP 定位 JSON
2. case10_assembly_vlm_hints.json    — PartB VLM 给的 ROI 提示
3. case10_assembly_largest_ic_box.png — PartB OpenCV 最大IC框图
4. case10_vlm_hints.json             — PartA VLM 给的 ROI 提示
5. case10_largest_ic_box.png         — PartA OpenCV 最大IC框图
6. step08_result.json                — 最终输出（pixel坐标必须一致）

## 准确性判断规则（auto_analyze.py 内置，已按用户要求调整）
- 像素容差 TOLERANCE_PX = **10**（已收紧，用户要求最多 10px）
- 连续失败阈值 FAILURE_STREAK_THRESHOLD = 2
- 只要 |dx| ≤ 10 且 |dy| ≤ 10 就视为通过；单次失败只记录，连续 ≥2 次才写入 needs_attention.json
- 容差收紧后，准确性判断更严格，优化时需特别小心不要让坐标偏移超过 10px

## 全自动 10 轮训练模式（train_loop.py）
用户可直接执行：
```powershell
python training_platform/train_loop.py --rounds 10
```
- 每轮 = 运行 agent → auto_analyze（10px 容差）→ Cursor Agent 必要时直接改代码
- 下一轮自动使用改后版本，相当于“跑完一轮就验收上一轮的收益”
- 所有改动必须写在被改文件顶部 + EFFICIENCY_LOG.md
- 如果连续 2 次失败，会写入 needs_attention.json，Cursor Agent 会重点处理（可能回滚）

## 已实施改进（2026-06-02，基于 20260602-125729-task）
**文件**：`agent/agent.py`
**v1 改动（已回滚）**：在 `_compact_context_for_step` 中增加“非 view_image 步骤激进裁剪”逻辑。
- 检测上一个 user turn 是否包含 `view_image`
- **Bug**：content 经常是 list，导致检测永远失败 → 几乎所有步骤都被误判为 non-view_image → 图片上下文被裁掉 → view_image 步骤卡住
**v2 修复（当前版本）**：
- 只在 `step_idx >= 10` 之后才考虑激进裁剪
- 只在“上一步 assistant 不是 view_image / image_url”时才触发
- 永远不裁剪带 `image_url` 的消息
- keep_recent=7（比 v1 的 6 更保守）
**预期收益**：仍然能减少简单步骤的 prefill，但不会破坏 view_image 上下文
**准确性影响**：无
**记录位置**：agent/agent.py 顶部已更新 ## Efficiency Change Log（含 v1/v2 历史）
**下次验证**：用 `train_loop.py --rounds 1` 跑新版本，确认不再卡在 view_image

**v3 新增（2026-06-02，基于 20260602-132839-task）**：
- 在 `_upsert_planner_step_message` 中增加 late-stage compact 逻辑
- plan_idx >= 3（进入 Part D）后，planner message 从多行全文压缩为单行
- **已回滚**（见下）

## 回滚记录（2026-06-02，基于 20260602-134019-task）

**现象对比**：

| Run | 总时间 | 步数 | 像素 | 结果 |
|-----|--------|------|------|------|
| 20260602-132839-task（v2+v3） | 346s | 15 | [2037,1114] | 准确 ✓ |
| 20260602-134019-task（v2+v3） | 374s | 21 | [2046,1247] | dy=133 失败 ✗ |
| 20260602-101045-task（基线） | 371s | 14 | [2038,1114] | 准确 ✓ |

**绕路行为（134019）**：
- `list_files` 被调用 3 次（规程外）
- PartA 多次 `save_text_file` 重写 hints（JSON 字段名不对：`largest_ic_hint` 而非 `approx_bbox_norm`）
- PartD 从 71s 膨胀到 114s
- finish 的 reasoning 写成了 `case10_dual_roi_layout`，实际走了 case12 链 → 说明上下文/指令已混乱

**根因**：
1. **v3 单行 planner** 丢掉了 `next_action_hint`（里面有「直接调 case12」「禁止 dual_roi」「禁止 list_files」等硬约束）
2. **v2 激进裁剪** 在 step≥10 时进一步丢掉早期 phase 的关键上下文，模型开始「探索」

**处置**：v2 + v3 已全部回滚，`agent/agent.py` 恢复为仅使用原有的 `_compact_message_window`。

**后续安全提速方向**（不动 planner hint）：
- ~~Phase 切换时重置 messages~~ → **已实现 `phase_isolated_context`（2026-06-04）**
- 缩短首轮 Task 注入的 `STANDARD_WORKFLOW.md` 体积（Part0 仍用完整 Task）
- 在 `STANDARD_WORKFLOW_PLAN.case011.json` 的 `next_action_hint` 里加强约束
- 工具层 schema 校验

## Phase 隔离 Worker 上下文（2026-06-04）

**开关**：默认开启；`VLM_PHASE_ISOLATED_CONTEXT=false` 或 CLI `--no-phase-isolation` 关闭。

**行为**：
- JSON plan 不变；Planner 仍是 Python 推进 `plan_idx`
- 同一个 VLM API；但 **part0 → partB → partA → partD** 切换时 **清空 messages[]**
- 新上下文 = system + `[phase-handoff]`（artifact 路径 + tp_id + INPUT_PATHS）+ 完整 `[planner-step]`
- part0 内两个 plan step **不重置**（保留 Part0 完整 Task）
- 日志：`workspace/runs/<id>/phase_context_resets.jsonl`

## Improvement Entries
<!-- New entries will be appended here by Cursor Agent after each analysis + implementation cycle -->

### 2026-06-08 | run 20260608-211213-task | A1 plan技能
- **Files changed**: `agent/prompts/plan_system_prompt.md` (new)
- **Change**: Added hard Tool Selection constraint (ban `run_python` for assembly TP marking; require `mark_tp_on_assembly_from_pdf_hit`) and Output Format hints JSON validation (`region_hint` / `visual_cues` / `reference_text` required).
- **Prompt size**: ~527 est. tokens (chars/4), well under 4000-token prefill budget.
- **Expected speed impact**: Fewer Part0 `run_python` detours and plan-guard retries on hints JSON; modest savings (~5–15s per run if wired into planner context).
- **Risk**: low — prompt-only; no golden/plan-guard/phase_isolation changes.

### 2026-06-09 | run 20260609-003043-task | A1+A2
- **Files changed**: `agent/tools/save_text_file.py`, `agent/prompts/system_prompt.md`, `agent/core/handoff_manager.py` (new), `agent/agent.py` (wire filter into phase handoff)
- **Change**: A1 — `execute()` pre-flight `ValueError` when hints JSON lacks non-empty `region_hint`/`visual_cues`/`reference_text` (or `relative_to_tp` for assembly); system prompt adds standard JSON templates. A2 — `HandoffManager.transition_phase()` filters `handoff_artifacts` via regex `.*(_opencv_debug|_largic_box|_debug)\.(png|jpg|json)$` before cross-phase context injection.
- **Expected speed impact**: ~5–20s/run — fewer plan-guard retry loops on partial hints writes; smaller phase-handoff prefill by omitting debug PNG/JSON intermediates.
- **Risk**: low — validation is stricter only on hints paths; golden ROI/JSON paths are preserved by the filter regex.

### 2026-06-09 | run 20260609-011939-task | A1 save_text_file schema
- **Files changed**: `agent/tools/save_text_file.py`, `agent/tools/validators.py`, `agent/prompts/part_a_hints.md` (new)
- **Change**: Exported `PART_A_HINTS_JSON_SCHEMA` + `validate_routing_params()` (Pydantic-backed, requires `region_hint`/`visual_cues`/`reference_text`); routing layer now rejects incomplete hints before registry.run; PartA system prompt adds JSON Schema + full compliant example.
- **Expected speed impact**: ~5–15s/run — fewer plan-guard / invalid-write retry loops on partial PartA hints JSON.
- **Risk**: low — stricter pre-flight only on hints paths; pixel output unchanged (10px tolerance).

### 2026-06-22 | run 20260622-143512-task | A1+A2
- **Files changed**: `agent/core/agent_loop.py` (new), `agent/config/phase_rules.yaml` (new), `agent/guards/tool_validator.py` (new), `agent/prompts/system_prompt.md` (new), `agent/agent.py`, `agent/prompts.py`, `requirements.txt`
- **Change**: A1 — `consecutive_no_tool_steps` counter in agent loop; when >3 inject system idle message + auto `view_image` fallback; `phase_rules.yaml` sets partB `max_idle_steps: 3` with fuse skip to next phase after fallback still idle. A2 — `validate_tool_call()` phase whitelist + jsonschema args check (hints `region_hint` etc.) before tool execution; `[TOOL_CONSTRAINTS]` block in system prompt.
- **Expected speed impact**: ~10–30s/run — fewer empty-turn LLM stalls in PartB; early rejection of illegal tool/args without full retry loops.
- **Risk**: low — partB fuse only after idle fallback exhausted; plan-guard/phase_isolated_context unchanged; golden 10px tolerance preserved.

### 2026-06-30 | run 20260630-162043-task | P0 prompt体积
- **Files changed**: `agent/agent.py`
- **Change**: Turn-0 prompt trim — skip Base64 embedding of input rasters (path-only + `view_image` on demand); replace inlined `STANDARD_WORKFLOW` body (>12k chars) with hard constraints + Part0 quickstart while keeping `workflow_doc` for targeted `read_text_file`.
- **Expected speed impact**: ~15–20s/run — Step 0/1 prompt drops from ~45k to ~10–12k est. tokens (smaller prefill/decode on cold start).
- **Risk**: low — Part0 binding constraints retained; images loaded via existing `view_image` tool; phase_isolated_context and plan-guard unchanged.

### 2026-07-02 | run 20260702-100013-task | P0 prompt体积
- **Files changed**: `agent/agent.py`
- **Change**: For `start_from_step3` / pre-marked Step02 runs only — trim system prompt after Step1 rules (drop Step2–8 protocol blocks); skip turn-0 full-flow boilerplate (Path discipline / Step2–8 mandate / Part0 footer); one-line global summary; tighter case12 tool-result compression (280 chars) for Step0→Step1 handoff.
- **Expected speed impact**: ~10–14s/run on 2-step case_002 path — static prompt ~6k→~2.8k est. tokens per step (~53% prefill reduction on repeated context); aligns with reflection target under 4k tokens/step.
- **Risk**: low — scoped to `_is_step3_premarked_case` only; full-flow tasks unchanged; planner-step hints and finish contract preserved.

### 2026-07-02 | run 20260702-102847-task | P0 流程编排 (phase_context_resets)
- **Files changed**: `agent/agent.py`
- **Change**: Fix finish-only `phase_context_resets` — remove `cur_group != "done"` guard so entering the finish step (plan complete) triggers the same isolated handoff as part transitions; inject `step08_result.json` pixel into handoff key facts.
- **Expected speed impact**: ~2–3s on 2-step Step3+ runs — finish-step prompt drops from ~4k to ~1k tokens (less prefill/decode on Step 1); `phase_context_resets.jsonl` will record the finish reset.
- **Risk**: low — only drops consumed align/emit history; structured state (pixel, artifact paths, planner finish hint) retained; finish-retry context preserved on subsequent steps.

### 2026-07-02 | run 20260702-104316-task | P0 prompt体积 (finish minimal output)
- **Files changed**: `agent/agent.py`
- **Change**: Finish-only phase handoff uses compact `_system_prompt_for_task` + minimal finish system block (skip INPUT_PATHS); `[planner-step]` injects mandatory minimal-output rules (finish tool only, reasoning ≤20 words); case12 align/emit tool results compressed to pixel/coords/confidence via workspace JSON (~120 chars).
- **Expected speed impact**: ~2.5–3.5s on 2-step Step3+ runs — finish prefill drops (compact system vs full on reset); decode ~200–300 tokens saved from shorter finish completion.
- **Risk**: low — output-format constraint only; pixel still sourced from step08_result.json; phase_isolated_context and finish contract unchanged.

## 2026-07-02 10:48 | autonomous_loop | kept
- run_id: `20260702-104316-task`
- actions: (none)
- verify pixel_ok: True
- verify wall_s: 12.3
- session_best_verify_s: 12.3
- verify report run: 20260702-104800-task

### 2026-07-02 | run 20260702-104813-task | P0 agent代码 (LLM API config)
- **Files changed**: `agent/llm_client.py`
- **Change**: Tool-turn `max_tokens` capped at 512; enable streaming with usage (`stream=True`, `stream_options.include_usage`); for Qwen3 hybrid models (`qwen3*`, `qwen-plus*`) explicitly disable default thinking via `extra_body.enable_thinking=false` + `chat_template_kwargs` (low-latency routing on DashScope/vLLM).
- **Expected speed impact**: ~2–2.5s/run on qwen3.6-plus — skip hidden reasoning decode on tool/finish turns; shorter completion budget; streaming lowers TTFT.
- **Risk**: low — thinking remains available when `VLM_ENABLE_THINKING=true`; 512-token cap sufficient for native tool calls; non-stream mocks/tests still work via `hasattr(raw, "choices")` fallback.

## 2026-07-02 10:52 | autonomous_loop | kept
- run_id: `20260702-104813-task`
- actions: (none)
- verify pixel_ok: True
- verify wall_s: 8.2
- session_best_verify_s: 8.2
- verify report run: 20260702-105223-task

### 2026-07-02 | run 20260702-105231-task | P0 prompt体积 (finish tool contract)
- **Files changed**: `agent/builtin_tools.py`
- **Change**: Tighten `finish` tool description + `answer` parameter schema — tool call only (zero assistant prose); minimal JSON `{"pixel":[x,y],"needs_user_help":false}`; explicitly forbid reasoning/confidence/tp_id and natural-language explanations.
- **Expected speed impact**: ~1.5–1.8s on 2-step finish-only step — completion_tokens ~95→~10–20 (decode is 79% of Step 1 in reflection run).
- **Risk**: low — `normalize_finish_arguments` and `_validate_finish_answer` unchanged; pixel still required from step08_result.json.

## 2026-07-02 10:54 | autonomous_loop | kept
- run_id: `20260702-105231-task`
- actions: (none)
- verify pixel_ok: True
- verify wall_s: 8.1
- session_best_verify_s: 8.1
- verify report run: 20260702-105430-task

### 2026-07-02 | run 20260702-105439-task | P0 流程编排 (auto-finish after emit_step08)
- **Files changed**: `agent/agent.py`
- **Change**: After successful `emit_step08_from_case12_aligned` when Part D plan artifacts are complete, invoke `finish` programmatically from `step08_result.json` pixel (same contract validation as LLM finish); skip redundant Step 1 LLM round.
- **Expected speed impact**: ~2.0s/run on 2-step Step3+ paths — eliminates entire finish-only LLM step (prefill + decode).
- **Risk**: low — gated on `_is_plan_step_done` + `_validate_skill_contract` + `_validate_finish_answer`; falls back to LLM finish if validation fails.

## 2026-07-02 10:56 | autonomous_loop | kept
- run_id: `20260702-105439-task`
- actions: (none)
- verify pixel_ok: True
- verify wall_s: 6.3
- session_best_verify_s: 6.3
- verify report run: 20260702-105636-task

### 2026-07-02 | run 20260702-105644-task | P0 prompt体积 (Step3+ minimal system)
- **Files changed**: `agent/agent.py`
- **Change**: For `_is_step3_premarked_case` only — replace generic system head (~7.6k chars / ~1.9k est. tokens) with case12-only compact prompt (align → emit → finish + partd tool whitelist); drop Part0/Step1–8 PCB boilerplate and unused phase tool-constraint examples.
- **Expected speed impact**: ~0.8–1.0s on Step3+ Step 0 — prompt_tokens ~4301→~2200–2500 (smaller prefill on case_002 fast path).
- **Risk**: low — scoped to pre-marked Step02 runs; full-flow tasks unchanged; planner-step hints and finish contract preserved.

### 2026-07-02 | run 20260702-110203-task | P0 prompt体积 (Step3+ turn-0 dedupe + slim tool schemas)
- **Files changed**: `agent/agent.py`
- **Change**: For `_is_step3_premarked_case` only — strip duplicated PART3 quickstart from task text; drop redundant `## Provided inputs` listing; keep slim INPUT_PATHS (anchor PNG keys only); expose zero-arg OpenAI schemas for case12 align/emit + compact finish params (defaults unchanged at execution).
- **Expected speed impact**: ~3–5s on Step3+ Step 0 — est. prompt_tokens ~2455→~1200–1500 (~40% prefill reduction); avoids iter-13 ultra-minimal revert path that removed all task context.
- **Risk**: low — task question + planner-step + system prompt retained; tool execution defaults unchanged; full-flow tasks untouched.
