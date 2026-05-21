---
name: pcb-testpoint-localization
description: Default PCB testpoint localization — engineer intent, schematic, assembly PDF, board photo → TP pixel on the board image. Read workflow_doc then this file; Part 0–D per STANDARD_WORKFLOW.md.
---

# PCB Testpoint Localization (Single Global Skill)

This file is the only global skill document for this project.
Case YAML should reference it via:

`inputs.skills_doc: ../../skills/SKILL.md`（若省略则由 **`load_task`** 从 `../../skills/` **自动填入**，见 `agent/config.py`。）

**模型如何使用本文**：首轮里 `skills_doc` 通常只以 `[file] skills_doc = <路径>` 出现，**正文不会自动注入**。**须**用 **`read_text_file`** 读入。与 **`workflow_doc`（`STANDARD_WORKFLOW.md`）** 同理：任务 YAML **可不再手写**这两项，只要 case 位于 `data/cases/<名称>/` 且存在 **`data/skills/`** 下对应文件。

**与本仓库默认规程的关系**：**各用例的标准主路径**见 **`STANDARD_WORKFLOW.md`**（运行时会出现在 **`inputs.workflow_doc`** 中，可自动注入）。**推荐顺序**：`read_text_file(workflow_doc)` → `read_text_file(skills_doc)`。  
- **Part 0–D**（原理图→TP、PDF 整页栅格、OpenCV 绿圈、双图最大 IC、`board_tp_marked`、Part D 双 ROI 等）**以 `workflow_doc` 为准**。  
- **`SKILL.md` 本文**侧重 **Step3–8 字段契约**、工具说明与 **历史附录**。  
- **最终交付**：在实物板工作底图上给出 **目标 TP 的像素位置**（Step8 / `finish`）。

**旧版 Step1 位号绿圈（`pdf_draw_circle_then_rasterize` 为主）**：仅适用于 **显式要求旧 Step1/2 链** 的历史任务；**默认流程不得**以该链替代 **`workflow_doc`** 中的 Part 0 OpenCV 绿圈规程。

**PDF → PNG 线条偏淡（尤其彩色/细线）**  
矢量光栅化时抗锯齿 + 极细线宽在像素上易被「冲淡」，观感不如 PDF 阅读器。可在 **`pdf_page_to_image`** / **`pdf_draw_circle_then_rasterize`** 上可选传入（PyMuPDF 渲染时生效）：**`graphics_min_line_width`**（例如 **0.35–0.75**）与 **`aa_level`**（**0–8**，较低更锐利，例如 **3–4**）。`pdf2image` 回退路径下 these knobs 不生效。

## Quick Start

**默认目标**：工程师意图 + 原理图 + **可搜索位号图 PDF** + **实物板图** → **目标 TP 在实物板工作底图上的像素坐标**（经 Part 0–D 与 `finish`）。

**默认执行顺序**（逐步约束见 **`STANDARD_WORKFLOW.md`**，经 **`inputs.workflow_doc`** 读取）：**Part 0** → **Part B** → **Part A** → **Part C** → **Part D（Step3–8，`mapping_method`=`case10_dual_roi_layout`）**。

任务应同时提供 **`inputs.workflow_doc`**（规程）与 **`inputs.skills_doc`**（本文）。二者可由 **`load_task` 自动注入**（见 `STANDARD_WORKFLOW.md` 文首），task 里只列数据文件即可。

## Current Input Convention (for this project)

本仓库 **标准主路径**（与各用例统一）所需输入与 **`STANDARD_WORKFLOW.md`** 一致，典型包括：

- **`user_measurement_question`**：工程师测量 / 调试意图（自然语言）。
- **`schematic_image`** 和/或 **`schematic_pdf`**（至少一种；PNG 只用 `view_image`；PDF 可 `search_pdf_text` 辅助）。
- **`assembly_drawing_pdf`**：带文本层的位号图 PDF（Part 0 搜索与整页栅格）。
- **`front_board_photo`**：实物板照片（Part A 中归一为横幅工作底图）。
- 可选 **`assembly_drawing`** 等 legacy 整页 PNG：仅作对照或 PDF 失败 fallback，**主路径**不以之替代 PDF 栅格（见 workflow）。

**读取顺序**：先 **`read_text_file`** 读 **`inputs.workflow_doc`**，再读 **`inputs.skills_doc`**。

---

### Legacy（中场切入，历史数据）

部分旧用例从 Step3 起跑，输入为 **已画好绿圈 + 红框** 的成图：

- `front_locator_marked` / `front_board_marked` 等（见后文 Step3 契约）。

### Deprecated（旧版「从零 Step1/2」链）

以下 **`locator_pdf` + Step1 优先 `pdf_draw_circle_then_rasterize`** 的全链 **不再作为默认**；**仅当任务明文要求历史 Step1/2** 时参考本文 **「Step1/2 I-O Contract」** 一节。**默认测点定位不得与 `workflow_doc` 中的 Part 0 混用两条主路径。**

1) Deprecated full-flow（旧 Step1–8，从零输入）：
- `locator_pdf`：即位号 PDF（旧 Step1 命名）
- `front_board_photo`
- 原理图 `schematic_pdf` 和/或 `schematic_image`
- `user_measurement_question` 和/或 `target_signal`

2) Legacy mid-flow entry (start from Step3):
- `front_locator_marked`: locator image already marked with:
  - red box = silkscreen‑clear anchor chip (same IC as board)
  - green circle = target test point
- `front_board_marked`: board image already marked with:
  - red box = same anchor chip as on locator
- `schematic_pdf`: schematic PDF

When **deprecated** full-flow entry is used, Step1/2 must first generate marked artifacts from **`locator_pdf`**,
then Step3 consumes those artifacts. **This is not the repo default** — the default is **`STANDARD_WORKFLOW.md`**.

Full-flow（旧链）下，语义上的 **`front_locator_marked`**（仅绿圈、标出目标 TP）对应文件名为
`debug/step01_locator_front_anchor.png`（见旧 Step1 输出）；**`front_board_marked`**
在 Step2 完成后对应 `debug/step02_board_front_anchor.png`。

## Step1/2 I-O Contract (Strict) — **历史 / 显式旧任务专用**

> **注意**：本节描述 **旧版**「`locator_pdf` + `pdf_draw_circle_then_rasterize`」Step1/2。**当前默认主路径**为 **`STANDARD_WORKFLOW.md`**（Part 0 OpenCV 绿圈等）。新任务 **勿**默认执行本节替代 Part 0。

### Step1 — 用户意图 → 原理图 TP → 位号图绿圈

**目标**：只解决「测哪里」：**在用户给定的问题（或 `target_signal`）下，原理图上对应哪一个测试点 `TPxxx`**；再在**位号图 PDF** 上找到该 `TPxxx` 的几何位置，导出正面页 PNG 并用**绿色圆圈**标出该 TP。

**工具基础**：`search_pdf_text` 返回 **`page` + `rect_pdf`**（PDF 点坐标，左上原点、y 向下）。**画绿圈并导出位号图 raster 时，优先用 `pdf_draw_circle_then_rasterize`**：传入与搜索相同的 `pdf_path`、`page`、`rect_pdf`（及与最终产物一致的 `dpi`，建议 **600**）、`out_path=debug/step01_locator_front_anchor.png`。该工具在 **PDF 坐标系内画圆**再 `get_pixmap`，与 WPS Ctrl+F 高亮同属文字层几何，**不经过手算 PNG 像素**。若该工具不可用或失败，再退化为：`pdf_page_to_image` 后按 `rect_pdf * (dpi/72)` 用 `annotate_image` / `run_python` 画绿圈，并自检圆心是否与 `view_image` 一致。

**Inputs（Step1 必填）**
- 原理图：`schematic_pdf` 和/或任务提供的**原理图栅格**（如 `schematic_image` PNG）；**栅格原理图禁止**对之使用 `search_pdf_text`，用 `view_image`。
- `locator_pdf`（位号图 PDF，常多页）
- **用户意图**，二选一或同时提供（同时时以任务描述为准）：
  - `user_measurement_question`（自然语言测量/调试问题），或
  - `target_signal`（网/信号名，便于 `search_pdf_text`）

**Recommended actions（顺序）**

1. **原理图 → 仅输出 TP 编号**  
   - 将 `user_measurement_question`（及/或 `target_signal`）与原理图一并作为推理输入：若是 **`schematic_pdf`**，可用 `search_pdf_text` 检索 net / `target_signal` / 关键词；若是 **PNG 等栅格原理图**（如 `schematic_image`），**禁止**对之 `search_pdf_text`，用 `view_image` 读图。必要时对 PDF **命中页**再 `pdf_page_to_image` + `view_image` 确认。  
   - **本阶段交付**：唯一主结论为 **`TPxxx`**（若存在多候选，在 JSON 中列出并说明取舍，但仍需最终选定一个用于后续位号图）。

2. **位号图 PDF → 几何定位 + 绿圈 PNG（强制优先本路径）**  
   - 对 `locator_pdf` 使用 `search_pdf_text` 检索 **`TPxxx`**（字面量；注意大小写时可 `case_sensitive=false`），得到 **`page`** 与 **`rect_pdf`**；可选 `out_json_path=debug/step01_locator_search.json` 落盘证据。  
   - **必须**调用 **`pdf_draw_circle_then_rasterize`**：`pdf_path=locator_pdf`，`page` 与搜索一致，`rect_pdf` 与命中一致，`dpi=600`（或与任务统一的其他 dpi），`out_path=debug/step01_locator_front_anchor.png`。若彩色丝印偏淡，可同时传 **`graphics_min_line_width`**（如 0.5）与 **`aa_level`**（如 3）。工具在 PDF 上画绿圈时：**仅传 `rect_pdf` 不传 `radius_pt`** 则圆半径约为命中框半边的 **0.9×**（较早年 **1.8×** 更小一圈、更贴 TP）；需要更大圈时显式传 **`radius_pt`**。在 PDF 点坐标上画**绿色描边圆**后再光栅化，**不得**先把空白页转图再手算 `rect_pdf*(dpi/72)` 除非本工具失败。  
   - 用 **`view_image`** 核对 `debug/step01_locator_front_anchor.png` 上绿圈是否落在 `TPxxx` 丝印处；偏差大则复核页码/命中是否唯一。  
   - **勿**画红框（红框属 Step2）。  
   - **禁止**仅依赖全文拼串定位而无 `rect_pdf`/可视校核（无文字层须改用整页图像流程或 OCR，并记入 `evidence`）。

**Required outputs**
- **`debug/step01_locator_front_anchor.png`**  
  - 语义别名：**`front_locator_marked`**（仅含目标 **TPxxx** 的**绿色圆圈**；尚**无**红框）。  
  - 供 **Step2** 必需输入；**Step3** 仍以 Step2 产出的 **`step02_*_anchor.png`**（含绿圈与红框）为主输入，请勿仅用 Step1 图做 Step3 映射。  
  - 弃用旧名 `debug/step01_locator_front_marked.png`；新任务请统一用本路径。
- **`debug/step01_signal_to_tp.json`**（建议字段）  
  - `user_measurement_question`（若有）、`target_signal`（若有）、`tp_id_or_ref`、`locator_page`、`evidence`（含检索片段、`rect_pdf` 或截图路径等）。

**Known pitfalls（难点，需自检）**
- 自然语言问题与 net/TP **一对多**：原理图与位号图均需证据；JSON 中写清依据。  
- 位号图 **无文本层**：`search_pdf_text` 无命中 → 必换图像/OCR/人工约定路径。  
- **同页多个相同丝印**或 **TP 字符串多处命中**：结合 `rect_pdf` 与周边丝印、或裁剪 `view_image` 判别。  
- **坐标 / 画偏**：**默认不允许**跳过 `pdf_draw_circle_then_rasterize`；若用手算像素，dpi 必须与导出图一致，并用 `view_image` 复核。  
- **顺序**：先产 **`debug/step01_locator_front_anchor.png`**（含绿圈），再在 Step3 类脚本里检测绿圈；禁止在空白 raster 上先跑「找绿色」。

### Step2 — 固定锚定红框（与测试点无关）

**目标**：在**不**改变绿圈的前提下，为「**两幅图中丝印最清楚、可唯一定位的那颗芯片**」添加红框（位号 Uxxx/丝印 + 实物上可读的同一丝印或封装标称）；位号图与实物图**各一张**，红框表示**同一颗**器件。  
**禁止**仅凭「面积最大」选锚；若最大芯片丝印模糊而旁边有明显 U 号/料号，应优先选丝印清晰的芯片。

**几何约束（强制）**  
画红框时 **不得** 对位号图 PNG、实物板照片做任何 **几何变换** 再落笔，红框坐标必须落在 **原始像素的坐标系**里（左上角原点，+x 向右，+y 向下，与 `image_info` / 文件直接 `imread` 一致）。  
**禁止**使用包括但不限于：`cv2.rotate` / `cv2.flip` / `transpose` / `warpAffine` / `warpPerspective` / `PIL.Image.rotate` 等改变像素网格与 TP/丝印对应关系的操作。允许 **仅** 读取原图、画矢量矩形、`imwrite` 保存；禁止为了「摆正」板子而旋转整图后标框。

**Inputs（必填）**
- `debug/step01_locator_front_anchor.png`（Step1 产出：仅绿圈）
- `front_board_photo`（例如 `PCBA_IMG.jpg`）

**推荐方法 — 实物图（PCBA，`debug/step02_board_front_anchor.png`）**  
这是全流程里最需要对齐丝印的一侧，建议固定为：

1. 对 **`front_board_photo` 整图**调用 **`view_image`**（必要时配合 **`crop_image` 只用于肉眼查看**，crop 结果**不得**代替最终全图标框产物）。在画面上 **直接读出** 锚定 IC 的丝印（如 `U801`、料号等）。  
2. 根据视觉判断 **封装外廓 + 可辨引脚外沿**，在纸上/心中确定整数像素 `x1,y1,x2,y2`（全图坐标）。  
3. 使用 **`run_python`**：`cv2.imread` 读 **未旋转的** 原图路径 → `cv2.rectangle(img, (x1,y1), (x2,y2), (0,0,255), 厚度 2–4)`（纯红，便于 Step3 HSV）→ `cv2.imwrite` 写到 **`debug/step02_board_front_anchor.png`**。`print` 出 `bbox` 以便写入 `step02_anchor_mapping.json`。  
4. 再 **`view_image`** 该输出文件，确认红框包住的正是刚才读到的丝印那一颗芯片。

**位号图与实物图分开估框（强制）**  
两幅图 **尺寸与透视不同**，**禁止**把在实物图上得到的 `bbox` **原样**用到 `debug/step01_locator_front_anchor.png` 上（或反之）。应对位号图 **单独** `view_image`，在 **3341×2267（或当前导出尺寸）** 坐标系下重新估计 `x1,y1,x2,y2`，再写入 `debug/step02_locator_front_anchor.png`。抄同一组整数坐标到两张图会导致位号图锚框完全错位。

位号图侧：在 **`debug/step01_locator_front_anchor.png`** 上可用 **`annotate_image`** 的 `bbox`，或同样 **`run_python` + `cv2.rectangle`** 后存 **`debug/step02_locator_front_anchor.png`**（**保留绿圈**），同样 **不得** 先旋转整图。

**Required actions（摘要）**
1. 识别丝印锚定芯片，在**未变换的**位号图上画红框并保留绿圈。  
2. 对实物图采用上节 **「view → 定边界 → run_python 画框」**，输出全尺寸 `step02_board_front_anchor.png`。  
3. **强制落盘**：两路径文件必须存在并各 `view_image` 自检；**禁止**仅 Markdown/JSON；**禁止**未产出前进入 Step3 / `run_step3_mapping` / `step03_prior_on_board.png`。

**Required outputs**
- 位号图：`debug/step02_locator_front_anchor.png`（**绿圈 + 红框**，文件必须存在）  
- 实物图：`debug/step02_board_front_anchor.png`（语义：**`front_board_marked`**，**红框**，文件必须存在）  
- 建议：`debug/step02_anchor_mapping.json`（`anchor_ref`, `locator_box`, `board_box` 等；坐标应与图上红框一致）

**Known pitfalls**
- **多颗尺寸相近**：以**丝印可对齐**为准，保证两张图红框为同一 refdes/同一可读标记。  
- **标注覆盖**：红框勿遮挡绿圈关键部分（必要时细线 1–2px）。  
- **跳过保存**：会导致 Step3 无法检测红锚、prior 错误——视为流程失败。  
- **旋转/裁剪后标框**：会导致与机械/相机像素不一致，**一律禁止**作为 Step2 交付。

## Effective Methods

### 1) Anchor-first localization

- Identify a **silkscreen‑clear** anchor chip on layout and the same chip on the real photo (match refdes/markings).
- Mark chip with a tight box that includes all pins.
- Treat this box as the primary coordinate anchor.

### 2) Progressive box refinement

- Start with coarse rectangle, then tighten to pin-inclusive edges.
- Validate containment explicitly (no pin pixels outside box).
- Use thin outlines (`1px` or `2px`) for visual precision.

### 3) Cross-image mapping

- Use layout-anchor box to photo-anchor box transform for initial TP prediction.
- Search only in a bounded local ROI around predicted TP.
- Never do TP selection from full-image global search unless local search fails.

### 4) Local feature matching around TP

- Build a TP neighborhood template from layout around the circled point.
- Remove annotation colors (red/green marks) before feature extraction.
- Compare candidates by local edge/shape overlap and distance regularization.

### 5) Semantic filtering (component vs test point)

- Prefer standalone circular exposed pads for TP candidates.
- Down-rank candidates embedded in pin rows or dense component-pad clusters.
- Use cues: circularity, area range, neighbor density, and isolation.

### 6) Multi-candidate relative-geometry scoring

- Extract multiple local silver points in real photo ROI.
- Assume each point as TP hypothesis and compare relative constellation to layout TP neighborhood.
- Rank hypotheses by trimmed nearest-neighbor geometry loss (lower is better).

## Standard Workflow

Copy this checklist and keep it updated during execution:

```text
Task Progress:
- [ ] Step 1: 用户意图/信号 → 原理图 TPxxx → 位号图绿圈 PNG
- [ ] Step 2: 绿圈位号图 + 实物图 → 丝印清晰同芯红框（**两路径 PNG 均已保存**）
- [ ] Step 3: Build layout->photo local mapping
- [ ] Step 4: Generate TP candidates in local ROI
- [ ] Step 5: Score by local features
- [ ] Step 6: Score by semantic TP constraints
- [ ] Step 7: Score by neighborhood relative geometry
- [ ] Step 8: Export final and debug outputs
```

### Step 1: User intent / signal → TP on schematic → green circle on locator raster

**Phase A — 只定 TP 编号**

- 输入：`user_measurement_question` 和/或 `target_signal`，以及原理图（`schematic_pdf` 和/或 `schematic_image` 等）。  
- PDF 原理图：可用 `search_pdf_text` 找 net/关键词/`target_signal`；栅格原理图：仅 **`view_image`**。锁定页码后对单页 `pdf_page_to_image` + `view_image` 直至确认 **TPxxx**。  
- 交付：明确的一个 **`tp_id_or_ref`**，写入后续 JSON。

**Phase B — 位号图几何 + 绿圈文件**

- 对 `locator_pdf`：`search_pdf_text` → **`page`** + **`rect_pdf`**（建议保存 `debug/step01_locator_search.json`）。  
- **`pdf_draw_circle_then_rasterize`**（`dpi=600`，`out_path=debug/step01_locator_front_anchor.png`），参数与上一步同一 `pdf_path`/`page`/`rect_pdf`；线淡时可加 **`graphics_min_line_width` / `aa_level`**（见文首说明）。失败时才退化为：`pdf_page_to_image` + `annotate_image` / `run_python` 按 `rect_pdf*(dpi/72)` 画绿圈并 `view_image` 自检。  
- 保存 **`debug/step01_signal_to_tp.json`**（建议含 `user_measurement_question`、`target_signal`、`tp_id_or_ref`、`locator_page`、`evidence`，证据含 `rect_pdf` 与工具调用说明）。

### Step 2: Silkscreen-clear anchor — red boxes (locator + board)

- 输入：**`debug/step01_locator_front_anchor.png`**、`front_board_photo`。  
- **禁止**对输入图做旋转、镜像、透视矫正后再画框；坐标系 = 原始 `imread` 像素。  
- **实物图（推荐）**：`view_image` 整板读丝印 → 确定 `x1,y1,x2,y2` → **`run_python`**：`cv2.rectangle` 纯红 BGR `(0,0,255)` → **`debug/step02_board_front_anchor.png`** → `view_image` 复核。  
- 位号图：`annotate_image` 的 `bbox` 或 **`run_python`** 画框 → **`debug/step02_locator_front_anchor.png`**（保留绿圈）。  
- **`view_image`** 两张产物；另存 **`debug/step02_anchor_mapping.json`**。  
- **未完成两 PNG 落盘前，不得**调用 `run_step3_mapping`、**`match_green_tp_roi_to_board`** 或生成 prior；**除非** `task.yaml` 明确跳过 Step2 且 Step3 改用 **`vlm_neighborhood_layout_match`**（纯 VLM 邻域排布，见 Path C）。

### Step 3: Build local mapping

Goal: map TP prior from locator image to board image: **where the green TP circle on the locator falls on the real board photo**.

**Path A — 绿圈邻域模板匹配（推荐先试）**  
不依赖红框几何精度时使用：

- 工具 **`match_green_tp_roi_to_board`**（见 `builtin_tools`）  
  - 输入：默认 **`debug/step01_locator_front_anchor.png`**（含绿圈）、**`front_board_photo`**（与 `inputs.front_board_photo` 同路径，全分辨率）。  
  - 做法：HSV 找绿圈 → 取外接矩形 + `margin_px` 裁图 → `cv2.inpaint` 去掉绿印 → 在 **缩小后的板图** 上 **多尺度** `matchTemplate`（`TM_CCOEFF_NORMED`）→ 将匹配窗映回全分辨率 → 用与红锚方案相同的 **标量 u,v** 把绿圈中心映射到板图 prior。  
  - 输出：**`debug/step03_tp_roi_template.png`**（模板）、**`debug/step03_tp_roi_match_debug.png`**（匹配窗示意）、**`debug/step03_mapping.json`**（含 `mapping_method`, `match_score_normed`，且含 **`locator_box` / `board_box` / `u` / `v` / `tp_prior_board`**，与 `run_step3_mapping` 契约一致）、**`debug/step03_prior_on_board.png`**。  
  - 若 `match_score_normed` 低于阈值，工具返回失败 —— 再改用 Path B。

**Path B — 双红框标量映射（经典）**  
与两颗锚定芯片的红框 + 绿圈中心：

- 输入：`debug/step02_locator_front_anchor.png`、`debug/step02_board_front_anchor.png`  
- 工具 **`run_step3_mapping`**（HSV 红框 + 绿圈中心 + u,v→px,py）。

**Path C — VLM 邻域形状与排布匹配（无 OpenCV 模板、无红框）**  
任务若要求**不用代码做局部模板匹配**，且不画 Step2 红框时：

- **禁止**调用 **`match_green_tp_roi_to_board`**（内部为 `matchTemplate` 等）。  
- **输入**：`debug/step01_locator_front_anchor.png`（绿圈位号图）、全分辨率 **`front_board_photo`**。  
- **禁止旋转/镜像/透视矫正**整张位号图或板照及任何中间结果（与 Step2 相同：仅用**原始像素系**下的 `crop_image` / `annotate_image`；`run_python` 也不得 `rotate` / `warpPerspective` 等）。板照里板子看起来歪斜时，在推理中脑补对应，**勿**把图「摆正」。  
- **位号图 ROI 必须以 Step1 绿圈为锚**；**固定顺序**：**(i)** 裁 ROI（工具默认约 **500×500**，可按需调大）→ **(ii)** ROI 内书面分析（`### Step3B`）→ **(iii)** **Step4（位号图局部）**：在 **`debug/step03_locator_roi.png`** 上，把绿圈周围 **灰黑色丝印线框**（内为红字位号/型号）里 **最清晰** 的若干框用 **`annotate_image` 红框 `bbox`** 套住（贴灰黑外沿），落盘 **`debug/step04_locator_landmarks.png`**（坐标须为 **ROI 图**像素，非整页位号图）→ **(iv)** 再在全板实物图上做 prior，写 `step03_mapping.json`。工具链会拦截跳过 3B/Step4 的 JSON、`step03_prior_on_board`、`step04_roi_crop` 与 `run_candidate_pipeline`。  
- **裁剪尺寸**：默认邻域约 **500×500**；不够可加大 `margin_px` / `min_side_px`。  
- **位号图邻域 ROI**：**优先**调用工具 **`crop_green_tp_neighborhood_on_locator`**（HSV 检测 Step1 绿圈质心后自动裁足够大的 `debug/step03_locator_roi.png`）。画绿圈时的 PDF 坐标不会进入对话，手填 `crop_image` 易错。  
- **`debug/step03_locator_roi.png`** 仍须来自**位号图**；板侧裁剪用 **`debug/step03_board_roi.png`** 等其它文件名。
- **在裁好的位号图 ROI 内完成「形状 + 排布」书面抽象后**，才将该签名用于实物图搜索。
- **产出**：与 Path B 相同键名的 **`debug/step03_mapping.json`**，且 **`mapping_method` = `vlm_neighborhood_layout_match`**。  
  - **先做** **`debug/step04_locator_landmarks.png`**：在 ROI 上为 **清晰灰黑丝印框**（内红字）描 **红框**（Step4）。  
  - `locator_box` 须包含绿圈中心，且与**绿圈锚定的邻域 ROI**一致或由该 ROI 合理外扩；`board_box` 为实物图上对应匹配邻域；二者均为全图坐标 `[x1,y1,x2,y2]`。  
  - `tp_locator_center` = 绿圈中心 `[gx,gy]`。  
  - **`u`、`v`、`tp_prior_board` 必须自洽**：  
    `u=(gx-lx1)/(lx2-lx1)`, `v=(gy-ly1)/(ly2-ly1)`,  
    `tp_prior_board = [bx1+u*(bx2-bx1), by1+v*(by2-by1)]`（与板上视觉点一致；若不一致则调整窗口而非捏造 JSON）。  
  - **`debug/step03_prior_on_board.png`**：全板上标出与 `tp_prior_board` 一致的 prior。  
  - 不要求 `step03_tp_roi_template.png` / `step03_tp_roi_match_debug.png`。

Preferred inputs for **Path B** full-flow entry:
- locator image: `debug/step02_locator_front_anchor.png`
- board image: `debug/step02_board_front_anchor.png`

Legacy equivalent:
- locator image: `front_locator_marked`
- board image: `front_board_marked`

Do this concretely:

Canonical implementation (match run `20260428-123221-task`):

- Use exactly these HSV thresholds first (before trying other heuristics):
  - red range #1: lower `[0, 100, 100]`, upper `[10, 255, 255]`
  - red range #2: lower `[170, 100, 100]`, upper `[180, 255, 255]`
  - green range: lower `[40, 100, 100]`, upper `[80, 255, 255]`
- For red boxes:
  - `mask_red = inRange(range1) OR inRange(range2)`
  - `findContours(mask_red, RETR_EXTERNAL, CHAIN_APPROX_SIMPLE)`
  - pick contour with max area
  - use `boundingRect` and convert to `[x1,y1,x2,y2] = [x, y, x+w, y+h]`
- For green TP center:
  - `mask_green = inRange(green_range)`
  - largest contour by area
  - centroid by moments:
    - `gx = m10/m00`, `gy = m01/m00`
- Mapping formula MUST be:
  - `u = (gx - lx1) / (lx2 - lx1)`
  - `v = (gy - ly1) / (ly2 - ly1)`
  - `px = bx1 + u * (bx2 - bx1)`
  - `py = by1 + v * (by2 - by1)`
- IMPORTANT interpretation:
  - `u` / `v` are normalized coordinates in the anchor-frame, not "inside-box-only" ratios.
  - `u` or `v` outside `[0, 1]` is valid when TP lies outside the red chip box in locator.
  - In this project setting, DO NOT clamp `u,v` by default. Preserve geometric extrapolation.
- Save mapping artifact:
  - `debug/step03_mapping.json`
  - include `locator_box`, `board_box`, `tp_locator_center`, `u`, `v`, `tp_prior_board`.
- Draw prior point on board image and save:
  - `debug/step03_prior_on_board.png`

Runtime hardening learned from run `20260428-123221-task`:

- Prefer reading inputs from `INPUT_PATHS["front_locator_marked"]` and
  `INPUT_PATHS["front_board_marked"]`.
- If relative path load fails, fallback to absolute path under `PROJECT_ROOT`.
- For Windows/Chinese filename robustness:
  - try `cv2.imread(path)` first;
  - if `None`, fallback to `PIL.Image.open(path)` + `np.array(...)`;
  - convert PIL RGB image to OpenCV BGR before OpenCV color processing:
    `img = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)`.
- Always log image load status and final image shapes before color thresholding.
- Avoid `cv2.transform` for Step3 point mapping. Use explicit scalar formula above
  to prevent channel/matrix-shape mismatch failures.

Do NOT switch to ad-hoc ORB/SIFT/manual guess unless the above fails. **Exception:**
the built-in tool **`match_green_tp_roi_to_board`** (green ROI template match) is an
approved first try when red anchors are untrusted; it still emits the same scalar
`u,v,tp_prior_board` JSON fields.

1) Detect red anchor box in `front_locator_marked` and `front_board_marked`.
   - Preferred: color threshold in HSV for red (two ranges around 0 and 180),
     then contour detection and pick the largest rectangular contour.
   - Output:
     - locator box: `(lx, ly, lw, lh)`
     - board box: `(bx, by, bw, bh)`

2) Detect the green TP circle center in locator image.
   - Threshold green in HSV, keep the main circular contour, compute centroid.
   - Output TP center in locator coords: `(gx, gy)`.

3) Compute normalized TP position within locator red box:
   - `u = (gx - lx) / lw`
   - `v = (gy - ly) / lh`
   - `u,v` may be outside `[0,1]` when TP is outside the anchor red box.
   - Do not clamp by default; keep extrapolation unless a separate outlier policy is explicitly enabled.

4) Map to board prior center using board red box:
   - `px = bx + u * bw`
   - `py = by + v * bh`
   - This `(px, py)` is the TP prior on real board image.

5) Validate mapping sanity:
   - TP prior is allowed to be outside board red box (same relative geometry extrapolation).
   - Validation should focus on detection quality (red/green masks, contour choice, centroid stability),
     not "must be inside red box".
   - If prior is implausible relative to board bounds or obvious board structures, re-check detections.

6) Export full-image prior debug overlay:
   - Draw board red box, prior point, and label `TP_PRIOR`.
   - Save `debug/step03_prior_on_board.png`.
   - Save mapping details from python to `debug/step03_mapping.json` with keys:
     - `locator_box`: `[lx,ly,lw,lh]`
     - `board_box`: `[bx,by,bw,bh]`
     - `tp_locator_center`: `[gx,gy]`
     - `u`, `v`
     - `tp_prior_board`: `[px,py]`
   - Write `(px, py)` and `(lx,ly,lw,lh)/(bx,by,bw,bh)` into `progress/step_03.md`.
   - MUST include coordinate convention explicitly:
     - origin = top-left
     - +x = right
     - +y = down
     - unit = pixel
     - and specify whether each coordinate belongs to locator image or board image.

Robust I/O note (Windows):
- If `cv2.imread(locator_path)` returns `None` but file exists, load with PIL:
  `np.array(Image.open(locator_path).convert("RGB"))` and then convert to BGR if needed.
- In python strings, prefer `INPUT_PATHS["front_locator_marked"]` and
  `INPUT_PATHS["front_board_marked"]` over hard-coded paths.

### Step 4: Generate local TP candidates

Goal: generate a bounded ROI around Step3 prior and enumerate candidate pads.

Do this concretely:

1) Build ROI centered at `(px, py)` from Step3.
   - Fixed half-size (mandatory for this project phase):
     - `rx = 50`
     - `ry = 50`
   - Therefore ROI size is fixed at `100 x 100` pixels.
   - ROI box:
     - `x1 = max(0, px-rx)`, `y1 = max(0, py-ry)`
     - `x2 = min(W, px+rx)`, `y2 = min(H, py+ry)`
   - Save ROI crop as `debug/step04_roi_crop.png`.

2) Candidate extraction inside ROI:
   - Convert ROI to HSV/gray.
   - Build bright-metal mask (high V, low-mid S) OR use adaptive threshold.
   - Connected components / contours to obtain blobs.
   - Keep candidates by area and circularity ranges; remove very large merged blobs.
   - Candidate definition (for this project):
     - A candidate is one connected blob center `(cx, cy)` in ROI that passes
       geometric filters:
       - area in configured range
       - circularity above configured threshold
       - not touching ROI boundary (optional but recommended)
     - Candidate center is contour/component centroid (moments) or bbox center
       when moments are unstable.

3) Candidate coordinates:
   - Store both ROI-local and board-global center:
     - local `(cx, cy)`
     - global `(x1+cx, y1+cy)`
   - Require at least top-N candidates (N>=3 if possible).

4) Export candidate debug:
   - Draw candidate IDs on ROI and full-board overlay.
   - Save `debug/step57_candidates_scored.png` (can be preliminary at this step,
     then overwritten after Step5/6/7 scoring).
   - Also save candidate list as machine-readable JSON (recommended):
     `debug/candidates_step4.json` with local/global coordinates.

5) Log required fields in `progress/step_04.md`:
   - prior `(px, py)`, ROI box `(x1,y1,x2,y2)`, candidate count, candidate list.

### Step 5: Local feature scoring

- Build layout TP neighborhood template.
- Candidate sourcing strategy (recommended for robustness):
  - Run multiple lightweight detectors inside the SAME ROI, then merge:
    1) adaptive-threshold + contour
    2) Canny-edge + contour
    3) bright-metal HSV mask + contour (optional fallback)
  - Use ONE unified candidate definition for all detectors:
    - candidate = blob center `(cx, cy)` that passes area/circularity filters.
  - Merge near-duplicate candidates across detectors by center distance threshold
    (for example, <= 4 px) and keep detector support count.
- Compare each merged candidate patch with edge/shape overlap score.
- Keep top-N for subsequent stages.
- Mandatory visualization for Step 5:
  - Draw ALL merged candidates (no omission) on ROI with IDs.
  - For each candidate, display Step5 local-feature score next to ID.
  - Compute and store adaptive visualization radius per candidate:
    - Preferred from contour area:
      - `r_eq = sqrt(area / pi)`
      - `r_vis = clamp(round(1.2 * r_eq), r_min, r_max)`
    - If contour area is unavailable, fallback to ROI-size rule:
      - `r_vis = clamp(round(0.02 * min(roi_w, roi_h)), 2, 6)`
  - Save as `debug/step05_local_feature_scores.png`.
  - Keep/refresh a table JSON such as `debug/step05_scores.json` with:
    `id`, `cx`, `cy`, `gx`, `gy`, `area`, `r_vis`, `score_local_feature`.

### Step 6: Semantic TP scoring

- Add TP-likelihood terms:
  - high circularity
  - moderate pad area
  - lower annulus neighbor density
  - not part of obvious component pin arrays
- IMPORTANT execution rule:
  - Step6 must score/filter the candidate list from Step5.
  - Do NOT re-detect a brand-new candidate set in Step6 unless Step5 has zero
    candidates. If Step5 is empty, explicitly record fallback reason in
    `progress/step_06.md`.
- Mandatory visualization for Step 6:
  - On the SAME candidate set from Step5, annotate semantic terms and total semantic score.
  - Highlight obvious non-TP types (pin-row-like / dense cluster) with low score.
  - Draw markers as SMALL DOTS centered on candidate centers (for precision),
    not large cross-hairs.
  - Reuse `r_vis` from Step5 for each candidate marker.
  - Only if `r_vis` is missing, compute fallback radius:
    - ROI: `clamp(round(0.02 * min(roi_w, roi_h)), 2, 6)`
    - Full board: `clamp(round(0.005 * min(W, H)), 3, 12)`
  - Save as `debug/step06_semantic_scores.png`.
  - Save/update JSON `debug/step06_scores.json` with:
    `id`, `r_vis`, `score_semantic`, and term breakdown.

### Step 7: Relative-geometry scoring

- Extract nearby reference points around layout TP.
- For each Step5/6 surviving photo TP hypothesis, compare relative vectors
  (angle + scaled distance) to layout.
- Use robust aggregate loss (trimmed mean) and rank candidates.
- Mandatory visualization for Step 7:
  - Draw candidate IDs and final rank on ROI and full-board overlays.
  - Reuse candidate `r_vis` from Step5/6 to keep marker scale consistent across steps.
  - Save final ranked debug image:
    `debug/step57_candidates_scored.png`.
  - Save/update JSON `debug/step07_scores.json` with:
    `id`, `score_geometry`, `final_score`, `rank`.
  - Step7 figure must still include ALL candidates considered in Step5/6/7.

### Step 8: Export outputs

Always export:
- Final mark image (single chosen TP).
- Debug image (candidate points + rank labels).
- Text report with:
  - candidate list
  - scores
  - chosen point
  - key transform parameters
- For this project, use:
  - `debug/step08_final_tp.png` for final red-circle annotation.
- Final marker radius rule (important):
  - Reuse winner candidate `r_vis` from Step5/6/7 for Step8 final circle radius.
  - If `r_vis` is missing, fallback to size-adaptive rule:
    - `r_final = clamp(round(0.005 * min(W, H)), 3, 12)` on full-board image.
  - Avoid oversized fixed radii (for example `radius=15`) unless the board
    resolution requires it by the adaptive formula.

## Tool Usage Contract (framework-specific)

Prefer this tool sequence in the agent runtime:

1. `list_files` to confirm assets.
2. `view_image` for locator and board images.
3. `image_info` for dimensions.
4. `crop_image` and `annotate_image` for visual verification.
5. `read_text_file` on extracted/available text files.
6. `run_python` for matching/scoring logic.
7. `finish` only after **`debug/step08_final_tp.png`**, **`debug/step08_result.json`**, and other required **`debug/`** artifacts exist; **`finish.answer.pixel`** must match the board frame (see `STANDARD_WORKFLOW` / agent contract).

### Path Discipline (critical)

- In `run_python`, use `INPUT_PATHS["front_locator_marked"]`,
  `INPUT_PATHS["front_board_marked"]`, `INPUT_PATHS["schematic_pdf"]`
  for source files whenever possible.
- `PROJECT_ROOT` and `WORKSPACE` variables are pre-injected; use them instead of
  hard-coded relative paths.
- For generated outputs, use paths relative to workspace only:
  - good: `progress/step_02.md`, `artifacts/tp_candidates.png`
  - avoid: `workspace/progress/step_02.md` (can cause nested paths)
- For Windows absolute paths in python strings, use raw strings (`r"..."`) if needed.

## Progress notes (`progress/step_XX.md`)

Optional. The agent **`finish` gate** checks **`debug/*.png` / `debug/*.json`** and the `finish` payload — not Markdown step logs. You may still use `save_text_file` for human-readable notes.

模板（如需记录）：

```markdown
# Step XX - <title>
- Inputs used:
- Method/tool calls:
- Key observations:
- Intermediate output (coords / bbox / file paths):
- Confidence:
- Next step:
```

## Output Contract

Primary deliverable: **target TP center `pixel: [x, y]`** on the **full landscape board raster** (same as `debug/step08_final_tp.png`), plus that PNG on disk.

When reporting results to user, include:
- Final **`pixel`** and marker radius (`r_vis` / `marker_radius`).
- Why this candidate won (feature/semantic/relation evidence).
- Paths for **`debug/step08_final_tp.png`**, **`debug/step08_result.json`**, and key prior debug art.

For this framework, `finish(answer)` should include at least:

- `tp_id`
- `camera_view`
- **`pixel`** — **required** when `needs_user_help` is not true; must match **`debug/step08_result.json`** (±1px) and align with the red marker on **`debug/step08_final_tp.png`**
- `confidence`
- `needs_user_help`
- `user_message`
- `reasoning`

## Failure Handling

- If candidate scores are too close, provide top-2 with both overlays and ask user to confirm.
- If ROI is noisy, tighten anchor mapping first instead of widening TP search globally.
- If no reliable TP candidate exists, return diagnostic image and exact reason (threshold too strict, merged blobs, insufficient local contrast).

