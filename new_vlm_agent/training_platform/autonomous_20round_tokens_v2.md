# 新一轮 20 轮 — 耗时与 Token 汇总

## Reflect 内容在哪看

每轮 **primary run** 结束后会写两份文件（同一目录）：

- Markdown（人类可读）：`workspace/runs/<primary_run_id>/vlm_self_reflection.md`
- JSON（含 usage / timing）：`workspace/runs/<primary_run_id>/vlm_self_reflection.json`

同目录还有 `apply_plan.json`（reflect 后生成的 Cursor 改动计划）。

示例：第 1 轮 → `F:\KPIT\VLM-test\test_platform_whole - v3 - final\workspace\runs\20260608-211213-task\vlm_self_reflection.md`

## 总计

| 指标 | 数值 |
|------|------|
| primary agent 主循环 | 5278.3s |
| primary reflection | 1891.3s |
| verify agent 主循环 | 5580.3s |
| Cursor apply | 3158.7s |
| **合计墙钟（上述四项）** | **15908.7s（≈4.42h）** |
| Qwen agent 主循环 tokens | 7,839,572 |
| Qwen reflection tokens | 211,110 |
| Qwen apply_plan tokens | 132,326 |
| Qwen verify agent tokens | 6,715,732 |
| **Qwen 每轮 iteration 合计** | **14,898,740** |

## 逐轮明细

| 轮次 | outcome | agent loop(s) | reflect(s) | verify(s) | apply(s) | agent tok | reflect tok | plan tok | verify tok | iter total tok | reflect 路径 |
|------|---------|---------------|------------|-----------|----------|-----------|-------------|----------|------------|----------------|--------------|
| 1 | kept | 200.5 | 94.6 | 216.1 | 112.0 | 343,027 | 9,909 | 5,960 | 392,609 | 751,505 | `runs/20260608-211213-task/vlm_self_reflection.md` |
| 2 | reverted_not_session_best | 281.1 | 91.2 | 278.4 | 227.1 | 393,377 | 9,944 | 7,511 | 338,857 | 749,689 | `runs/20260608-212343-task/vlm_self_reflection.md` |
| 3 | skipped_inaccurate_pre_apply | 437.7 | 92.7 | 0.0 | 0.0 | 519,398 | 12,488 | 6,148 | 0 | 538,034 | `runs/20260608-213952-task/vlm_self_reflection.md` |
| 4 | reverted_not_session_best | 209.3 | 91.2 | 441.0 | 206.8 | 293,270 | 9,506 | 5,893 | 247,428 | 556,097 | `runs/20260608-214944-task/vlm_self_reflection.md` |
| 5 | reverted_not_session_best | 220.3 | 89.1 | 286.3 | 123.1 | 392,178 | 9,865 | 7,154 | 292,447 | 701,644 | `runs/20260608-220636-task/vlm_self_reflection.md` |
| 6 | reverted_pixel | 394.4 | 93.4 | 182.7 | 251.9 | 473,010 | 12,852 | 6,882 | 331,178 | 823,922 | `runs/20260608-221959-task/vlm_self_reflection.md` |
| 7 | reverted_not_session_best | 228.9 | 81.2 | 275.7 | 201.3 | 403,175 | 9,652 | 7,527 | 417,993 | 838,347 | `runs/20260608-223640-task/vlm_self_reflection.md` |
| 8 | reverted_not_session_best | 234.4 | 82.5 | 483.3 | 177.6 | 288,165 | 9,129 | 6,369 | 401,932 | 705,595 | `runs/20260608-225118-task/vlm_self_reflection.md` |
| 9 | skipped_inaccurate_pre_apply | 259.1 | 97.7 | 0.0 | 0.0 | 404,706 | 10,473 | 6,556 | 0 | 421,735 | `runs/20260608-230850-task/vlm_self_reflection.md` |
| 10 | reverted_not_session_best | 220.9 | 102.3 | 268.4 | 106.4 | 393,528 | 10,593 | 7,055 | 327,994 | 739,170 | `runs/20260608-231553-task/vlm_self_reflection.md` |
| 11 | reverted_pixel | 253.6 | 83.4 | 755.8 | 159.9 | 401,887 | 9,681 | 7,060 | 532,835 | 951,463 | `runs/20260608-232854-task/vlm_self_reflection.md` |
| 12 | reverted_not_session_best | 199.6 | 124.8 | 281.0 | 128.2 | 343,334 | 11,585 | 6,168 | 346,890 | 707,977 | `runs/20260608-235110-task/vlm_self_reflection.md` |
| 13 | reverted_not_session_best | 235.0 | 98.3 | 262.4 | 139.7 | 353,916 | 10,393 | 6,084 | 403,788 | 774,181 | `runs/20260609-000427-task/vlm_self_reflection.md` |
| 14 | reverted_not_session_best | 207.8 | 93.8 | 238.9 | 166.4 | 343,400 | 9,871 | 6,187 | 404,348 | 763,806 | `runs/20260609-001749-task/vlm_self_reflection.md` |
| 15 | kept | 220.7 | 95.2 | 214.8 | 141.3 | 306,164 | 9,935 | 6,141 | 393,005 | 715,245 | `runs/20260609-003043-task/vlm_self_reflection.md` |
| 16 | reverted_not_session_best | 334.6 | 83.4 | 215.6 | 207.7 | 507,075 | 11,391 | 6,039 | 344,250 | 868,755 | `runs/20260609-004303-task/vlm_self_reflection.md` |
| 17 | reverted_pixel | 400.7 | 102.6 | 429.4 | 288.1 | 479,592 | 12,635 | 6,383 | 543,112 | 1,041,722 | `runs/20260609-005809-task/vlm_self_reflection.md` |
| 18 | kept | 233.2 | 88.1 | 197.0 | 231.8 | 404,307 | 9,881 | 7,239 | 294,825 | 716,252 | `runs/20260609-011939-task/vlm_self_reflection.md` |
| 19 | reverted_not_session_best | 264.4 | 117.6 | 252.8 | 207.7 | 394,786 | 11,378 | 7,233 | 355,985 | 769,382 | `runs/20260609-013335-task/vlm_self_reflection.md` |
| 20 | reverted_not_session_best | 242.3 | 88.5 | 300.7 | 81.8 | 401,277 | 9,949 | 6,737 | 346,256 | 764,219 | `runs/20260609-014901-task/vlm_self_reflection.md` |
