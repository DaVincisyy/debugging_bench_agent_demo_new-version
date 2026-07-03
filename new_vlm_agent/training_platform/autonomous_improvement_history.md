# 历史自主改进记录（最近轮次，供避免重复失败方案）

规则：若某 action 曾在 `kept_in_repo=false` 或 outcome 为负收益 中出现，不要再次提出实质相同的改动；可提出不同路径或明确说明为何上次失败本次可避免。

## 第 10 轮 — 未执行改动（skipped_inaccurate_pre_apply）
- primary: `20260630-155732-task`
- primary total: 86.5153s
- 改动/计划：
  - [工具契约] 改造save_text_file为直写接口，消除LLM空转 → `agent/tools/file_io.py, agent/skills/file_write_skill.json`
  - [prompt体积] 对search_pdf_text结果实施动态截断与Token限制 → `agent/tools/pdf_search.py, agent/prompts/system_context.md`
- 反思摘要：
  # VLM 运行后效率反思 — `20260630-155732-task`
  
  - model: `qwen3.6-plus`
  - generated: 2026-06-30T16:00:53
  - reflection time: 115.1s
  
  ## 耗时瓶颈
  1. **Step 0 (part0)**：总耗时 25.94s（占全流程 30%），其中 LLM 解码高达 19.71s。Prompt 膨胀至 45674 tokens，是绝对的性能瓶颈。
  2. **Step 5 (parta) & Step 2 (partb)**：仅调用 `save_text_file`，却分别耗时 10.08s 与 8.74s，LLM 耗时占比均 >99%。中间状态落盘引发完整推理循环，严重拖慢节奏。
  3. **Step 8 (partd)**：`finish` 步骤耗时 9.99s，LLM 解码 6.38s。收尾阶段仍在进行重度文本生成，未利用已计算坐标直接输出。
  4. **Step 7 (partd)**：对齐与发射工具 (`case12_build_and_align_from_step02_an…

## 第 11 轮 — 负收益：verify 像素未过 10px，已 git revert
- primary: `20260630-160203-task`
- verify: `20260630-161414-task`
- primary total: 159.7507s
- verify total: 84.4605s
- verify−primary: -75.3s
- verify pixel distance: 147.7px (ok=False)
- 反思摘要：
  # VLM 运行后效率反思 — `20260630-160203-task`
  
  - model: `qwen3.6-plus`
  - generated: 2026-06-30T16:07:35
  - reflection time: 172.7s
  
  ## 耗时瓶颈
  1. **Step 3 (32.71s)**：调用 `save_text_file`，LLM 耗时占比 99.9%。其中 Prefill 异常高达 22.30s（Prompt 仅 9.4k），Decode 10.40s，为全链路最慢单步。
  2. **Step 1 (32.45s)**：组合调用 `search_pdf_text`/`mark_tp_on_assembly_from_pdf_hit`/`save_text_file`，Prompt 膨胀至 48.5k tokens，LLM 推理耗时 30.59s。
  3. **Step 0 (19.83s)**：初始 `view_image` 携带 45.7k prompt，LLM 耗时 19.80s，Prefill (6.33s) 与 Decode (13.47s) 共同构成高延…

## 第 12 轮 — 未执行改动（skipped_inaccurate_pre_apply）
- primary: `20260630-161542-task`
- primary total: 97.9682s
- 改动/计划：
  - [工具契约] 为save_text_file添加强制结构化输出与Token限制 → `agent/tools/save_text_file.py, data/skills/tool_contracts/save_text_file.yaml`
  - [prompt体积] 压缩初始系统提示词并启用VLM图像Token降采样 → `agent/prompts/system_prompt_base.md, agent/config/vlm_image_config.yaml`
- 反思摘要：
  # VLM 运行后效率反思 — `20260630-161542-task`
  
  - model: `qwen3.6-plus`
  - generated: 2026-06-30T16:19:15
  - reflection time: 115.2s
  
  ## 耗时瓶颈
  1. **Step 1 (21.45s)**：LLM 解码耗时 17.02s，Prompt 达 48,476 tokens，Completion 766 tokens。`search_pdf_text` 与 `mark_tp_on_assembly_from_pdf_hit` 执行后，模型生成大量中间文本，占据总耗时 22%。
  2. **Step 0 (16.03s)**：Prefill 耗时 8.06s + Decode 7.93s。初始 Prompt 45,674 tokens 导致首字延迟极高，仅调用 `view_image` 却消耗大量算力，占总耗时 16%。
  3. **Step 3 & Step 6 (合计 ~24.8s)**：两步均仅调用 `save_text_file`，但 LLM 解码分别耗时 12.53s 和…

## 第 14 轮 — 负收益：verify 像素未过 10px，已 git revert
- primary: `20260630-162907-task`
- verify: `20260630-163649-task`
- primary total: 103.498s
- verify total: 77.3215s
- verify−primary: -26.2s
- verify pixel distance: 32.4px (ok=False)
- 反思摘要：
  # VLM 运行后效率反思 — `20260630-162907-task`
  
  - model: `qwen3.6-plus`
  - generated: 2026-06-30T16:32:51
  - reflection time: 120.4s
  
  ## 耗时瓶颈
  1. **Step 2 (17.39s)**：LLM decode 耗时 14.56s（占单步 83%），prompt_tokens 达 13367。单步内串行调用 `search_pdf_text`、`mark_tp_on_assembly_from_pdf_hit`、`save_text_file`，模型生成大量中间推理与格式包装文本。
  2. **Step 8 (14.23s)**：仅调用 `save_text_file`，但 LLM decode 高达 13.73s（618 tokens）。模型在确定性文件保存前进行了过度冗长的上下文总结或冗余校验。
  3. **Step 11 (10.36s)**：`finish` 阶段，prefill 耗时 3.79s（全 run 最高之一），decode 6.56s。Phase 累积…

## 第 15 轮 — 未执行改动（skipped_inaccurate_pre_apply）
- primary: `20260630-163810-task`
- primary total: 88.8309s
- 改动/计划：
  - [prompt体积] 约束 save_text_file 输出格式为纯 JSON 坐标 → `agent/prompts/system_prompt.md, agent/tools/save_text_file.py`
  - [流程编排] 过滤 Phase Handoff 中间调试文件以压缩上下文 → `agent/core/context_manager.py, agent/config/phase_config.yaml`
- 反思摘要：
  # VLM 运行后效率反思 — `20260630-163810-task`
  
  - model: `qwen3.6-plus`
  - generated: 2026-06-30T16:41:46
  - reflection time: 127.6s
  
  ## 耗时瓶颈
  1. **Step 4 (`save_text_file`)**：16.35s。LLM 解码独占 16.34s，`completion_tokens` 达 743，为全链路最慢单步。
  2. **Step 2 (`search_pdf_text` + `mark_tp...` + `save_text_file`)**：15.08s。LLM 耗时 13.33s，`prompt_tokens` 高达 13589，多工具串行叠加导致上下文严重膨胀。
  3. **Step 0 (`search_pdf_text` ×2)**：11.45s。单步内重复调用同一检索工具 2 次，LLM 耗时 11.16s，存在明显调度冗余。
  4. **Step 7 (`save_text_file`)**：10.22s。LLM 解码 10.20s，`com…

## 第 4 轮 — 负收益/中性：像素 OK 但 verify 未刷新 session best，已 revert（337.4s >= best 14.5s）
- primary: `20260702-101919-task`
- verify: `20260702-102307-task`
- primary total: 12.7256s
- verify total: 337.3776s
- verify−primary: +324.6s
- verify pixel distance: 0.0px (ok=True)
- 反思摘要：
  # VLM 运行后效率反思 — `20260702-101919-task`
  
  - model: `qwen3.6-plus`
  - generated: 2026-07-02T10:20:47
  - reflection time: 74.9s
  
  ## 耗时瓶颈
  1. **Step 1 LLM 解码耗时过长**：`step:1` 调用 `finish` 工具，LLM 解码耗时达 `7.4667s`（生成 336 tokens），占全量运行时间 `58.7%`。模型吞吐约 45 tok/s 属正常水平，核心瓶颈在于生成了大量非必要推理文本。
  2. **Step 0 LLM 预填充与解码叠加**：`step:0` 调用 `case12_build_and_align_from_step02_anchors` 与 `emit_step08_from_case12_aligned`，prefill 耗时 `1.1661s`（处理 4259 tokens），decode 耗时 `3.1556s`，合计占全量 `34.0%`。
  3. **Prompt 体积持续高位**：两步 prompt_token…

## 第 7 轮 — 负收益/中性：像素 OK 但 verify 未刷新 session best，已 revert（11.8s >= best 13.9s）
- primary: `20260702-103832-task`
- verify: `20260702-104302-task`
- primary total: 17.5568s
- verify total: 11.7684s
- verify−primary: -5.8s
- verify pixel distance: 0.0px (ok=True)
- 改动/计划：
  - [agent代码] 启用LLM客户端KV Cache复用参数 → `agent/llm_client.py`
- 反思摘要：
  # VLM 运行后效率反思 — `20260702-103832-task`
  
  - model: `qwen3.6-plus`
  - generated: 2026-07-02T10:40:37
  - reflection time: 108.0s
  
  ## 耗时瓶颈
  1. **Step 0 LLM Prefill 耗时过高 (6.39s)**：占 Step 0 总耗时 63.8%。4259 tokens 的 Prompt 未命中缓存或存在冷启动开销，导致首字延迟显著，是本次 Run 的最大耗时来源。
  2. **Step 1 LLM Decode 异常缓慢 (5.33s)**：仅生成 240 tokens 却耗时 5.33s（吞吐约 45 tokens/s），远低于常规 VLM 性能基线，可能受强格式约束、并发限流或流式未启用影响。
  3. **Prompt 累积膨胀 (4259 → 5107 tokens)**：Step 1 的 Prompt 较 Step 0 增加 848 tokens，直接推高 Step 1 的 Prefill (2.20s) 与整体 LLM 耗时，上下文管理缺乏有效裁剪…

## 第 8 轮 — 正收益：保留改动，verify 9.1s < 原 best 13.9s
- primary: `20260702-104316-task`
- verify: `20260702-104800-task`
- primary total: 12.2577s
- verify total: 9.1433s
- verify−primary: -3.1s
- verify pixel distance: 0.0px (ok=True)
- 反思摘要：
  # VLM 运行后效率反思 — `20260702-104316-task`
  
  - model: `qwen3.6-plus`
  - generated: 2026-07-02T10:45:03
  - reflection time: 95.1s
  
  ## 耗时瓶颈
  1. **Step 1 `finish` 工具 LLM 解码耗时过长**：Step 1 总耗时 6.576s，其中 LLM 解码占 6.5664s（prompt 5107 tokens, completion 335 tokens），占总 run 时长的 53.6%，是绝对瓶颈。
  2. **Step 0 LLM 预填充与解码叠加耗时**：Step 0 调用 `case12_build_and_align_from_step02_anchors` 与 `emit_step08_from_case12_aligned`，LLM 耗时 4.9214s（prefill 1.6981s + decode 3.2222s），占总时长 40.2%。
  3. **Step 0 工具链执行开销**：Step 0 工具执行耗时 0.7583s，虽绝对…

## 第 9 轮 — 正收益：保留改动，verify 5.2s < 原 best 12.3s
- primary: `20260702-104813-task`
- verify: `20260702-105223-task`
- primary total: 8.4735s
- verify total: 5.2256s
- verify−primary: -3.2s
- verify pixel distance: 0.0px (ok=True)
- 反思摘要：
  # VLM 运行后效率反思 — `20260702-104813-task`
  
  - model: `qwen3.6-plus`
  - generated: 2026-07-02T10:49:52
  - reflection time: 90.5s
  
  ## 耗时瓶颈
  1. **LLM Decode 延迟主导整体耗时**：Step 0 与 Step 1 的 LLM 推理耗时分别为 `3.7641s` 和 `3.8376s`，合计占总耗时 `8.4735s` 的 90% 以上。模型生成仅 134/186 tokens 的响应耗时过长，是绝对性能瓶颈。
  2. **Step 1 纯 LLM 决策开销过大**：Step 1 仅调用 `finish` 工具（执行 `0.0052s`），但 LLM 仍消耗 `3.8376s` 生成指令。Prompt 达 `2821` tokens，Prefill/Decode 效率低下。
  3. **Step 0 工具串行阻塞**：`case12_build_and_align_from_step02_anchors` 与 `emit_step08_from_case12_…

## 第 10 轮 — 正收益：保留改动，verify 5.2s < 原 best 8.2s
- primary: `20260702-105231-task`
- verify: `20260702-105430-task`
- primary total: 5.1412s
- verify total: 5.2329s
- verify−primary: +0.1s
- verify pixel distance: 0.0px (ok=True)
- 反思摘要：
  # VLM 运行后效率反思 — `20260702-105231-task`
  
  - model: `qwen3.6-plus`
  - generated: 2026-07-02T10:52:57
  - reflection time: 21.0s
  
  ## 耗时瓶颈
  
  1.  **Step 1 LLM 解码耗时过长**：Step 1 中 `llm_s` 为 2.6677s，其中 `llm_est_decode_s` 高达 2.1111s。尽管生成了 95 个 token（包含最终结论），但相对于简单的 `finish` 动作，解码效率偏低，占该步总耗时的 79%。
  2.  **Step 0 Prompt 预填充（Prefill）开销大**：Step 0 的 `llm_est_prefill_s` 为 1.1151s，对应 `prompt_tokens` 4261。这是整个 Run 中最大的单次 Prefill 开销，表明初始上下文或工具描述体积较大，导致首字延迟高。
  3.  **Step 0 工具执行耗时显著**：Step 0 中 `tools_s` 为 0.814s，主要消耗在 `case…

## 第 11 轮 — 正收益：保留改动，verify 3.4s < 原 best 8.1s
- primary: `20260702-105439-task`
- verify: `20260702-105636-task`
- primary total: 4.9053s
- verify total: 3.42s
- verify−primary: -1.5s
- verify pixel distance: 0.0px (ok=True)
- 反思摘要：
  # VLM 运行后效率反思 — `20260702-105439-task`
  
  - model: `qwen3.6-plus`
  - generated: 2026-07-02T10:55:26
  - reflection time: 42.2s
  
  ## 耗时瓶颈
  
  1.  **Step 0 LLM Prefill 开销过大**：Step 0 总耗时 2.90s，其中 `llm_est_prefill_s` 高达 **1.60s**。尽管 Prompt Tokens 为 4301（中等规模），但预填充时间占据了该步总耗时的 55%，是单次推理中最显著的延迟来源。
  2.  **Step 1 LLM 推理效率低**：Step 1 仅调用 `finish` 工具，总耗时 2.00s，其中 `llm_s` 占 **1.99s**。值得注意的是，Decode 时间 (`0.98s`) 几乎等于 Prefill 时间 (`1.01s`)，对于仅生成 44 个 token 的任务而言，模型响应速度异常缓慢，可能存在服务端排队或模型加载冷启动问题。
  3.  **工具执行相对高效但占比被掩盖**：Step…

## 第 13 轮 — 负收益/中性：像素 OK 但 verify 未刷新 session best，已 revert（50.4s >= best 5.7s）
- primary: `20260702-105853-task`
- verify: `20260702-110110-task`
- primary total: 2.5822s
- verify total: 50.3545s
- verify−primary: +47.8s
- verify pixel distance: 0.0px (ok=True)
- 反思摘要：
  # VLM 运行后效率反思 — `20260702-105853-task`
  
  - model: `qwen3.6-plus`
  - generated: 2026-07-02T10:59:15
  - reflection time: 19.1s
  
  ## 耗时瓶颈
  
  1.  **LLM Prefill 延迟过高 (Step 0)**：在唯一的执行步骤中，`llm_est_prefill_s` 高达 **1.2633秒**，占该步总耗时 (2.58s) 的 ~49%。尽管 prompt_tokens 仅为 2455，但预填充效率低下是主要瓶颈。
  2.  **工具链串行执行开销 (Step 0)**：`tools_s` 为 **0.7837秒**。Step 0 同时调用了 `case12_build_and_align_from_step02_anchors`、`emit_step08_from_case12_aligned` 和 `finish`。虽然总工具耗时不高，但在单步内串联多个逻辑操作（对齐+发射结果+结束）可能导致内部等待或序列化开销。
  3.  **LLM Decode 相对占比正…
