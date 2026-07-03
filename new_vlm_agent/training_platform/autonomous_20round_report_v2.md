# 新一轮 Autonomous 20 轮 — 改前/改后耗时

会话起始: 2026-06-08T21:12:11 | 轮次数: 20

改前 = primary 的 `part_timing.total_s`（不含 reflect）。改后 = verify 的 `part_timing.total_s`。

| 轮次 | outcome | 改前 agent loop(s) | 改后 verify(s) | Δ(s) | 像素 | deliverable | 改动 |
|------|---------|-------------------|----------------|------|------|-------------|------|
| 1 | kept | 200.5 | 216.1 | 15.6 | 1.0px (OK) | OK | 强化Plan提示词约束：禁用run_python替代专用工具并强制hin |
| 2 | reverted_not_session_best | 281.1 | 278.4 | -2.7 | 1.0px (OK) | OK | 强化part0阶段工具白名单与执行顺序约束; 前置save_text_file的JSON Schema必填字段校验 |
| 3 | skipped_inaccurate_pre_apply | 437.7 | None | None | Nonepx (FAIL) | None | 增加PartB连续无工具调用步数安全阀; 约束Step1 save_text_file输出长度与格式 |
| 4 | reverted_not_session_best | 209.3 | 441.0 | 231.7 | 1.0px (OK) | OK | 为save_text_file添加强类型JSON Schema预校验; 裁剪早期步骤与finish阶段的冗余上下文注入 |
| 5 | reverted_not_session_best | 220.3 | 286.3 | 66.0 | 1.0px (OK) | OK | 添加工具调用前置本地校验器拦截无效参数; 为 save_text_file 添加强制 JSON Schema 预校 |
| 6 | reverted_pixel | 394.4 | 182.7 | -211.7 | Nonepx (FAIL) | step08_result.json not produced in this run | Part0 连续无工具步安全熔断机制; Phase 切换时上下文强制结构化摘要 |
| 7 | reverted_not_session_best | 228.9 | 275.7 | 46.9 | 1.0px (OK) | OK | 硬编码 part0 阶段工具白名单拦截非法路由; 将 save_text_file JSON 契约前置至工具描述与 Pha |
| 8 | reverted_not_session_best | 234.4 | 483.3 | 249.0 | 1.0px (OK) | OK | Phase切换上下文压缩与冗余文件剔除; save_text_file 前置 JSON 键约束与防拦截 |
| 9 | skipped_inaccurate_pre_apply | 259.1 | None | None | Nonepx (FAIL) | None | 引入强类型 hints 输出契约替代通用文本保存; 强化 part0 阶段 Plan 约束与非法工具拦截 |
| 10 | reverted_not_session_best | 220.9 | 268.4 | 47.5 | 1.0px (OK) | OK | 强化Part0工具白名单与save_text_file前置校验; 合并PartA连续save_text_file调用为单次写入 |
| 11 | reverted_pixel | 253.6 | 755.8 | 502.1 | Nonepx (FAIL) | step08_result.json not produced in this run | 实施Phase切换时的上下文动态裁剪与Base64路径替换; 在System Prompt前置Plan-Guard负向示例与工具契约约 |
| 12 | reverted_not_session_best | 199.6 | 281.0 | 81.5 | 1.0px (OK) | OK | 为 save_text_file 添加强制 JSON Schema 预校; Phase 切换时实施上下文动态裁剪策略 |
| 13 | reverted_not_session_best | 235.0 | 262.4 | 27.5 | 1.0px (OK) | OK | 固化part0工具链并移除run_python权限; 为save_text_file注入JSON Schema强校验 |
| 14 | reverted_not_session_best | 207.8 | 238.9 | 31.1 | 1.0px (OK) | OK | 修正 part0 阶段工具白名单与 save_text_file 必填字; 为 save_text_file 启用 JSON Schema 响应格式 |
| 15 | kept | 220.7 | 214.8 | -5.9 | 1.0px (OK) | OK | 强化 save_text_file JSON 必填字段约束与前置校验; Phase 切换时过滤 handoff_artifacts 中的调试中间 |
| 16 | reverted_not_session_best | 334.6 | 215.6 | -119.0 | 1.0px (OK) | OK | PartB阶段纯推理步数限制与视觉工具强制注入; Phase Handoff 上下文动态裁剪与冗余数据剥离 |
| 17 | reverted_pixel | 400.7 | 429.4 | 28.7 | Nonepx (FAIL) | step08_result.json not produced in this run | PartB连续无工具调用步数熔断机制; save_text_file调用前JSON Schema前置校验 |
| 18 | kept | 233.2 | 197.0 | -36.2 | 1.0px (OK) | OK | 为 save_text_file 注入强类型 JSON Schema 与 |
| 19 | reverted_not_session_best | 264.4 | 252.8 | -11.6 | 1.0px (OK) | OK | 限制save_text_file输出长度并强制hint JSON Sch; 固化part0阶段PDF命中后直连mark_tp工具链 |
| 20 | reverted_not_session_best | 242.3 | 300.7 | 58.4 | 1.0px (OK) | OK | 强化Plan-Guard前置规则与save_text_file字段约束 |
