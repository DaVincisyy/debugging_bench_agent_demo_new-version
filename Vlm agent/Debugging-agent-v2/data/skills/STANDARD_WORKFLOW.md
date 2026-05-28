# 标准测点定位流程（全项目默认）

> **文件位置**：`data/skills/STANDARD_WORKFLOW.md`。任务 **`task.yaml` 不必**写出 `workflow_doc` / `skills_doc`：只要 case 目录在 `data/cases/<名称>/` 下，`load_task` 会自动填入 `../../skills/STANDARD_WORKFLOW.md` 与 **`SKILL.md`**。若仍需覆盖，可在 `inputs` 里显式指定路径。

**目标**：在工程师自然语言意图 + 原理图 + 可搜索位号图 PDF + 实物板图等输入下，经 VLM 与 OpenCV 协作，得到 **目标测试点在实物板图像素坐标系中的位置**（全流程以 `finish` 及 Step8 产物收敛）。

本文件定义 **本仓库默认、无预画框** 的完整 agent 规程：**Part 0（原理图→TP→PDF→OpenCV 绿圈）→ Part B（位号最大 IC）→ Part A（实物最大 IC）→ Part C（step02 + board_tp_marked）→ Part D（默认：`mapping_method` = `case12_step02_opencv_ic_align`，case12 Step2 抽象图 + **双 IC 红框 OpenCV 对齐** → Step8 → `finish`）**。  
**备用 Part D**（显式声明时）：**双 ROI / `case10_dual_roi_layout`**（Step3–8 原主路径）。  
调试产物文件名仍沿用历史前缀 **`case10_*`** / **`step*`**，与 `agent` 与脚本实现一致。

**与文档的关系**：步骤级细节 **以本文为准**；**Step3–8** 字段名、工具形态等通用契约见 **`SKILL.md`**。若 **`SKILL.md`** 后文的旧版 **Step1（`pdf_draw_circle_then_rasterize`）全链** 与本文冲突，**以本文为准**（旧链仅作历史参考，见 `SKILL.md` 标注）。

---

**任务类型（重要）**：**默认（`STANDARD_WORKFLOW`）**：**Part 0–C** 不变；**Part D** = **`case12_step02_opencv_ic_align`**（**case12 Step2 图 + OpenCV 双红框对齐** → **`case12_board_approx_overlay_opencv.png`** → **`step08_*` → `finish`**），见下文 **「Part D — 默认：`case12_step02_opencv_ic_align`」**。**备用**：若任务显式要求 **`mapping_method: case10_dual_roi_layout`**，则 **Part D-alt** 仍走 **Step3–8 双 ROI / 路径 A / B**（`step03_mapping` 含全量键、`step04_*`、`step05`、`step57` 等），见 **「Part D-alt」**。

**Part 0 须先做 VLM 选点**：**`INPUT_PATHS["user_measurement_question"]`** 加上原理图材料（**下述 `schematic_image` / `schematic_pdf` 至少一种**）→ **`view_image` / `read_text_file` / （可选）`search_pdf_text`（仅当存在可检索 `schematic_pdf`）** → **`save_text_file` → `debug/case10_signal_to_tp.json`**（**`tp_id_or_ref`** 为 **唯一**后续位号图 PDF 搜索词）。**再**使用 **`assembly_drawing_pdf`** + **`search_pdf_text`（query=`tp_id_or_ref`）** → **工作底图**：**默认** **`pdf_page_to_image`（或等价 `fitz` 栅格）** → **`debug/case10_assembly_drawing.png`**；**唯一例外**：若 Step0B 选定 **`page_pdf == 1`**（1-based）**且** **`INPUT_PATHS["assembly_drawing_page1_png"]`** 存在（由任务提供、通常为更清晰的第一页整页导出 PNG，如 **`位号图7.29_01(12).png`**），**则** **不必**再对该页调用 **`pdf_page_to_image`**——**复制**该文件为 **`debug/case10_assembly_drawing.png`**（**`shutil.copy2` 或 `cv2` 读写均可**），并在 **`stdout` / `progress`** 写明 **`assembly_source=assembly_drawing_page1_png`**。**若** **`page_pdf != 1`**，**忽略**该键，**必须**仍对 **`page_pdf`** 做 **`pdf_page_to_image`**。**若** 无该输入或**未确认**第一页命中，**不得**用任意预导出 PNG 替代 PDF 栅格。**禁止**用 `pdf_draw_circle_then_rasterize` 作 **唯一**最终 TP 标记；**权威绿圈**在 **`case10_assembly_drawing_tp_marked.png`**。**case12** 等 **仅 Step2 / `embed_workflow` 中场切入**、**不跑 Part 0** 的任务 **无需**提供本键。

**整场执行顺序（强制）**：**Part 0 → Part B → Part A → Part C → Part D**。不得先做实物 Part A 再做位号 Part B；**Part D 仅在 Part C 完成后开始**（默认走 case graph + OpenCV IC 对齐；**勿**在未产出 **`step02_*_anchor.png`** 时开始 Part D）。

**Part 0 — 原理图→`TPxxx` 后接 PDF**：**`case10_signal_to_tp.json`** → **`search_pdf_text`** → 选中 **`hits[].page`**；**`page_pdf==1` 且存在 `assembly_drawing_page1_png`** → **复制** → **`case10_assembly_drawing.png`**，**否则** **`pdf_page_to_image`**（**dpi=864**）→ **`case10_assembly_drawing.png`**；映射 **`rect_pdf`** → **`tp_work_roi`** → OpenCV 绿圈。

## 输入
- **`user_measurement_question`** — **必需**（字符串）：工程师测量 / 调试意图。
- **`schematic_image`** — **与 `schematic_pdf` 二选一或同时提供**。
  - **`schematic_image`**：原理图栅格（PNG 等），**`view_image`**；**禁止**对栅格 **`search_pdf_text`**。
  - **`schematic_pdf`**（可选）：可检索原理图 PDF；可作 **`search_pdf_text`** / **`pdf_page_to_image`** 与 PNG **互证**，**不**免除在 Part 0 落盘 **`tp_id_or_ref`** 的 VLM 推理。
- **`assembly_drawing_pdf`**（`INPUT_PATHS["assembly_drawing_pdf"]`）— **必需**：可搜索文本的**位号/装配图** PDF；**`search_pdf_text`** 用 **`case10_signal_to_tp.json`** 的 **`tp_id_or_ref`** 定位 **`page`**，再 **`pdf_page_to_image`** → **`case10_assembly_drawing.png`**（**或**见下项在第一页时的 shortcut）。
- **`assembly_drawing_page1_png`**（`INPUT_PATHS["assembly_drawing_page1_png"]`）— **可选**：**PDF 第一页**的**整页**、**高清晰**位号栅格（任务目录下文件名可自拟，如 **`位号图7.29_01(12).png`**）。**仅当** Step0B 选定 **`page_pdf == 1`** 时允许用作 **`debug/case10_assembly_drawing.png`** 的**唯一**来源并**跳过**该页的 **`pdf_page_to_image`**；**`page_pdf > 1` 时必须忽略**。**须**与 **`assembly_drawing_pdf` 第 1 页**几何对齐（整页可视区域）；若 Step0C 绿圈与 **`rect_pdf` 映射**明显错位，应改回 **`pdf_page_to_image(dpi=864)`** 重跑底图。**全量 Part 0** 用例均可按需加入；**case12 仅中场链**可不配。
- **`front_board_photo`**（`INPUT_PATHS["front_board_photo"]`）— **必需**：实物板图。
- **`assembly_drawing`**（`INPUT_PATHS["assembly_drawing"]`）— **可选 / legacy**：整页位号 PNG，**仅**对照或 PDF 栅格失败 fallback；**主路径禁止**用其覆盖 **`case10_assembly_drawing.png`**（**本键有别于** **`assembly_drawing_page1_png`**：后者仅在 **`page_pdf==1`** 时经规程显式允许替代栅格）。
- 规程与 Step3–8 说明由运行环境注入 **`workflow_doc`**、`skills_doc`（见文首）；**实物图 Part A** 须 **横幅**工作流；**位号图禁止旋转**。

## 当前启用｜VLM 理解与 OpenCV（必须接上参数）
- **Part A / Part B**：OpenCV 不得默认全图面积最大；**Part A** 与 **Part B** 均 **`vlm_roi`（JSON）→ 对称扩 `work_roi`**，**分割与轮廓仅**在 **`work_roi` 子图**内；候选择优 **禁止**几何中心锚点（条款同原文）。**全图兜底**仅当工作台/ROI 失效且已在 stdout **声明原因**。
- **Part 0**：PDF **仅搜索与矩阵映射**；**圆心与半径**来自 **PNG 子图 OpenCV**（圆度 + 面积带，同旧「TP 圆拟合」精神）。
- **修框路径**：**Part 0**：`search_pdf_text` → **`page_pdf`** → **`case10_assembly_drawing.png`**（**`page_pdf==1` 且有 `assembly_drawing_page1_png` 则复制；否则** **`pdf_page_to_image`**）→ **`rect_pdf`→ PNG ROI** → OpenCV `minEnclosingCircle` → 绿圈**；**Part B**：VLM hints → **`vlm_roi` → `work_roi`（对称扩边）** → OpenCV → 红框 annotate；**Part A**：`vlm_roi` → **`work_roi`** → OpenCV `boundingRect` → 红框 annotate；**Part C**：复制到 **`step02_*`**。

## 对话体积与 `view_image`（避免 provider 单次请求超限）
- 多模态接口对**单条请求**体积极常有限制；反复把 **全尺寸**（如 4K 级）PNG 经 `view_image` 再塞进下一轮上下文，容易导致 **`chat` 失败**（例如网关 `max bytes to buffer` / 500）。**禁止在无关步骤重复全图 `view_image`。**
- **必须 `view_image` 的时点**（本用例限定）：**Part 0** — **`debug/case10_assembly_drawing_tp_marked.png`** 写出后 **一次** QC。**Part B** — **`debug/case10_assembly_drawing_tp_marked.png`** 在 StepB1 前 **至多一次**；**`debug/case10_assembly_largest_ic_box.png`** 写出后 **一次** QC（**须**核对 **整页最大封装 IC 已被红框完整套住**，见 StepB3 清单）。**Part A** — **`debug/case10_board_landscape.png`** 在 Step1 前 **至多一次**；**`debug/case10_largest_ic_box.png`** 写出后 **一次** QC。**Part C** — **`debug/step02_locator_front_anchor.png`**（或等价成品路径）写出后 **至多一次** QC。**Part D** — **`debug/step04_locator_roi_refs.png`**（**路径 A：OpenCV 多锚点图，必须**）与 **`debug/step04_roi_crop.png`**（**StepD4.5 双 ROI 对照，必须**，**小图**）；可选 **额外至多一次** 裸 **`debug/step04_locator_roi_crop.png`**；**路径 A StepD4.5B** — 写出 **`debug/step04_dual_roi_approx_only.png`**（**VLM tp + ref approx 叠图**）后 **必须** **`view_image` 一次** 做 **与 JSON / 位号 refs 是否一致** 的自检；若 **修正 approx**，可 **再 view 新版叠图至多一次**。**fallback** 如启用 **`step04_dual_roi_direct_candidates.png`**，**须**对该图 **`view_image`** 后再写 pick JSON。若 **未走** StepD4.5 而 **仅**规律法，locator 仍 **必须**，board ROI 裁块 **至多一次**（OpenCV 前 QC）。
- **不要**在每次微调 OpenCV 参数后都对 **`case10_board_landscape.png`** 或 **位号全图** 再 `view_image`；**不要**对同一张全板图在同一流程里反复 `view_image`。
- **`debug/case10_opencv_debug.png`**（及位号图 debug）：优先依赖 **`run_python` 的 stdout**（候选 **面积、bbox、宽高比 AR、是否糊满 work_roi**）。若必须看图，用 **`crop_image`** 只裁 **IC 邻域小块**，或在 `run_python` 内 **`cv2.resize` 写出缩小版** debug 再 `view_image`，**避免**再大的全幅板图进入对话。
- OpenCV 迭代：**在同一 `run_python` 内**分支/重试并 `print` 判据；**不要**为「再多看一眼」无意义地追加全图 `view_image`。

---

## Part 0 — 位号图：**原理图→`TPxxx` → PDF 搜该 TP → 栅格命中页 → `case10_assembly_drawing.png` → OpenCV 绿圈**（整场**第一步**）

### Step0 — 段 A — **用户意图 + 原理图 → 唯一测试点编号（尚未碰位号图 PDF）**
- **必读**：**`INPUT_PATHS["user_measurement_question"]`**；原理图材料：**`INPUT_PATHS["schematic_image"]`** 和/或 **`INPUT_PATHS["schematic_pdf"]`**（至少其一须可读）。
- **`view_image`**：对 **`schematic_image`**（若有）与工程师问题一并推理；若有 **`schematic_pdf`** 而无 PNG，可对 **`pdf_page_to_image`** 出的关键页再 **`view_image`**。
- **`search_pdf_text`**：**仅**对 **`schematic_pdf`**（若有）使用，用于网标 / 丝印 / `TP` 串等**辅助检索**；**禁止**对栅格 **`schematic_image`** 调用。
- **禁止**跳过本段、仅在 `task.yaml` 或对话里假定某固定 **`tp_id_or_ref`** 而不落盘 **`case10_signal_to_tp.json`**。
- **`save_text_file` → `debug/case10_signal_to_tp.json`**（**合法 JSON**）：至少含 **`user_measurement_question`**（原文回显）、**`tp_id_or_ref`**（**完整** `TPxxx`，供 Step0B **`search_pdf_text` 的 query**）、**`evidence`** 或 **`evidence_zh`**（原理图判读：网络/器件/图示 TP 与问题的对应）。后续 Part 0 **只能**以本文件中的 **`tp_id_or_ref`** 为 **权威**目标测试点。

**产出（段 B 起）**：**`debug/case10_assembly_drawing_tp_marked.png`** = **`case10_assembly_drawing.png`**（**整页 = PDF 命中页 `pdf_page_to_image`（dpi=864）**，**或**在 **`page_pdf==1`** 时经 **`assembly_drawing_page1_png` 复制**）+ **绿色闭合圆**（**目标 `tp_id_or_ref`**）。绿圈 **必须**对应 **圆焊盘类轮廓**（见 Step0C 圆度与外接圆比过滤），**不得**套在非圆图形上。**每次运行必落盘**：**`debug/case10_target_tp_work_roi.png`** = 默认 **`tp_work_roi`（half=50，名义 100×100；贴边 clamp 后可能略小）** 在原图上的 **BGR 裁切**，供核对是否圈到邻居。**不要**用 `pdf_draw_circle_then_rasterize` 作为 **唯一**最终标记。

### Step0B — PDF 文本搜索（**须先于底图栅格**；**query = `case10_signal_to_tp.json` 的 `tp_id_or_ref`**）
- **`read_text_file`**：**`debug/case10_signal_to_tp.json`** → 取 **`tp_id_or_ref`**（须为非空字符串，如 **`TP415`**）。
- **`search_pdf_text`**：`pdf_path` = **`INPUT_PATHS["assembly_drawing_pdf"]`**，`query` **优先** = 上述 **`tp_id_or_ref` 全文**（如 **`TP1`**、**`TP415`**）。**工具层**：`query` 匹配 **`TP`+数字** 时，**整标号匹配**——剔除 PyMuPDF 子串假阳（例如搜 **`TP1`** 时 **不会**把 **`TP105`** / **`TP10`** 里嵌的 **`TP1`** 当真命中）；**仍须**以 JSON 里 **`tp_id_or_ref`** 为唯一权威串。若无 hit，**仅当** `tp_id_or_ref` 形如 **`TP`+数字** 时，可再试 **纯数字**（如 **`415`**、**`1`**）并 **严守**下条多义规则；**`out_json_path`=`debug/case10_target_tp_pdf_search.json`**。
- **纯数字 query 多义（必守）**：页面上 **任意子串** 命中（其它位号/页码）都会产生 noise；**禁止**仅用短数字 **`query`** 后 **默认 `hits[0]`**。须在 **`hits[].snippet` / `match`** 中选 **明确对应完整 `tp_id_or_ref` 丝印** 的一项，或在 **`debug/case10_target_tp_pdf_pick.md`** 写明 **为何该 `rect_pdf` 即目标 TP**（可与 **全文 `tp_id_or_ref`** 的专用搜索 **rect 对齐/最近邻** 互证）。
- **多命中**：在正文 + **`debug/case10_target_tp_pdf_pick.md`** 写明所选 **`page`（1-based，与 JSON `hits[].page` 一致）+ `rect_pdf` + snippet**。**0 hit**：不得编造坐标；**不得**继续假定某静态 PNG 为底图。
- **选定 hit 后**：记下 **`page_pdf = hits[k]["page"]`**，供 **Step0A** 与 **Step0C** 使用 **同一页**。

### Step0A — 位号 **工作底图 PNG**（**整页 = PDF 第 `page_pdf` 页**）
- **Shortcut（第一页 + 预导出清晰 PNG）**：若 **`page_pdf == 1`** **且** **`INPUT_PATHS.get("assembly_drawing_page1_png")`** 为非空路径且文件存在，**则** **`shutil.copy2`**（推荐）或等价读写到 **`debug/case10_assembly_drawing.png`**。**`stdout`/`progress` 必填**：**`page_pdf=1`**、**`assembly_source=assembly_drawing_page1_png`**、源文件路径。**不要**再对同一页调用 **`pdf_page_to_image`**。**自检**：该 PNG **应**与 **864 dpi 整页栅格**在 **W×H 与纵横比**上一致或极接近，以便 Step0C 中 **`sx,sy`** 与 **`rect_pdf`** 对齐；若 OpenCV 绿圈相对丝印**系统偏移**，**丢弃 shortcut 输出**，改走下方 **`pdf_page_to_image(dpi=864)`** 重写 **`case10_assembly_drawing.png`** 后重跑 Step0C。
- **`pdf_page_to_image`**（无 shortcut 或 shortcut 已回退时；推荐内置工具）：`pdf_path` = **`INPUT_PATHS["assembly_drawing_pdf"]`**，**`page` = `page_pdf`**（与 Step0B 所选 hit），**`out_path`=`debug/case10_assembly_drawing.png`**，**`dpi` 固定 864**（≈ **12 px/pt**，与 **`INPUT_PATHS["assembly_drawing"]`** 历史整页 PNG 常见导出尺度一致，**避免**与旧图/旧像素手记整板缩放偏差）。**说明**：**同一轮内**只要 PNG 是 **该页整页栅格** 且 Step0C 用 **`sx=W/pw`、`sy=H/ph`**（`W,H` 为当前图实际像素），**PDF `rect_pdf` 与当前底图自洽**——不会因 dpi 不同而在「本图内部」平移错位；**864** 作为本 case **与 legacy PNG 及历史 run 对齐**的契约值。**禁止**用 legacy **`assembly_drawing`** 键在 **`page_pdf!=1`** 或 **未满足 shortcut 条件**时覆盖主路径。可选 **`graphics_min_line_width`**（如 **0.5**）、**`aa_level`**（如 **3**）以使细线清晰。
- **等价 `run_python`**（无 shortcut 或 shortcut 已回退时）：`fitz.open` → `load_page(page_pdf-1)` → **`get_pixmap(matrix=fitz.Matrix(dpi/72, dpi/72), alpha=False)`**（**`dpi=864`**）→ 写入 **同一路径**；须与 **`pdf_page_to_image(dpi=864)`** 几何一致。
- **`image_info`**：`debug/case10_assembly_drawing.png` 的 **W×H**；**`stdout`/`progress`** 写明 **`page_pdf`**、**`dpi=864` 或 `dpi=na(assembly_drawing_page1_png)`**，便于复查 **PNG 与 `page.rect` 对齐**（`sx≈W/pw`，`sy≈H/ph`，`pw,ph` 为该页 **`page.rect`** 宽高 pt）。
- **Fallback（仅 PDF 栅格失败，且未使用或未允许 shortcut）**：在 **`progress`/`engineer_note`** 声明原因后，**才允许**使用 **`INPUT_PATHS["assembly_drawing"]`** 复制为 **`case10_assembly_drawing.png`**（须 **仍与命中页同源**）。

### Step0C — `rect_pdf` → PNG 像素 + OpenCV 定圆
- **`tp_work_roi` 尺寸（硬约束）**：**固定 100×100 px**（`half=50`），以 **PDF 映射到 PNG 后的文字框中心** 为锚，**clamp** 贴边；**禁止**为找圆焊盘自行扩大到 **160×160** 或更大。**0 候选**时仅在 **该 100×100（或 clamp 后实际窗口）** 内调整阈值/形态学并 **`stdout` 声明**；仍失败则 **`raise RuntimeError("case10 target TP: no pad contour after pdf-guided ROI")`**，不得扩大 ROI。
- **`run_python`**（单脚本；**勿 `raise SystemExit`**）：
  1. 读 **`case10_assembly_drawing.png`**（`W,H`）— **须为 Step0A 产物**：**要么** PDF 第 **`page_pdf`** 页 **`dpi=864` 整页栅格**，**要么**（仅 **`page_pdf==1`**）**经 **`assembly_drawing_page1_png` 复制**的整页图；**`fitz.open`** PDF，**`load_page(page_pdf - 1)`**，`page.rect` → **`pw,ph`**（pt）。**`search_json` 须读 `hits` 数组**，取与 Step0B **同一**选定 hit 的 **`rect_pdf`**（**无** `top_hits` 键）。
  2. **映射**：`sx=W/float(pw)`，`sy=H/float(ph)`；**`rect_pdf`** → PNG 整数框 **`[x0,y0,x1,y1]`**，**clamp**。（**前提**：PNG 为 **该页整页** 渲染，与 **`page.rect`** 同尺度；若用过 **`assembly_drawing_page1_png`**、**`assembly_drawing` fallback** 或其它非 864 栅格，须在 **`case10_target_tp_pdf_pick.md` / progress** 写明 **偏移或 DPI 不一致**；**错位则回退 Step0A，改用 **`pdf_page_to_image`（dpi=864）** 重写底图。**）
  3. **构造 `tp_work_roi`**：`cx = int(round((x0 + x1) / 2))`，`cy = int(round((y0 + y1) / 2))`，**半边 `half=50`** → `wl,wt,wr,wb = cx-50, cy-50, cx+50, cy+50`，**clamp** 到图内（贴边若使窗口不足 100，以 clamp 后窗口为准）。
  4. **`work_roi` 裁切落盘（必做）**：从 **`full`** 取 **`roi_bgr = full[wt:wb, wl:wr]`**（OpenCV 行 `y`、列 `x`），**`cv2.imwrite("debug/case10_target_tp_work_roi.png", roi_bgr)`**。**禁止** `roi_bgr = full`、`imwrite` 整图或 **`roi = img` 之类占位** —— `case10_target_tp_work_roi.png` **必须** 与 **`(wl,wt,wr,wb)`** 子窗口 **像素尺寸一致**（非全图尺寸）。**每次运行都必须写出**；名义上为 **100×100**，clamp 后宽高可能 **小于 100**，以实际像素为准。**`stdout`** 须同时写明 **文件路径**（**`case10_target_tp_work_roi.png`**）与 **`size=WxH`**，**禁止**只打印尺寸而不 `imwrite`、或令人生疑的泛化句（如仅 `Saved work_roi: 100x100` 而无路径）。
  5. 在 **步骤 4 的 ROI 子图** → 灰度 → **`THRESH_BINARY_INV`** → **弱 dilate（≤3×3, iter≤1）** → **`findContours`**。**目标 TP 在位号图上必须是「圆焊盘/圆环」类轮廓**：**禁止**把矩形丝印、走线、文字笔画等 **非圆闭合形状** 当选中对象后再强行 `minEnclosingCircle` 画绿圈（外观会像「圈住了根本不是圆的东西」）。**硬过滤（须实现， stdout 打印每项）**：
     - **圆度** `circ = 4π·area/perimeter²`，要求 **`circ ≥ 0.72`**（圆=1；细长条→0）。
     - **`boundingRect` 短边/长边** ≥ **0.72**（近正方形外包，排除长条）。
     - **圆拟合一致**：设 `(xc,yc), r = minEnclosingCircle`，要求 **`area / (π r²)` ∈ [0.40, 1.05]`**（实心圆盘约 1；镂空圆环可偏低，但不得与细长条套大圆混淆——若 `circ` 已低应已被剔除）。
     仅 **同时满足** 以上条的轮廓进入候选；**多候选时** 再取 **圆心距 ROI 内参考点** **最近** 者：参考点为 **PDF 映射文字框中心**在 ROI 内 **`(cx - wl, cy - wt)`**（与步骤 3 的 `cx,cy` 一致）；若该点落在 ROI 外则用 **ROI 几何中心** `((wr-wl)/2, (wb-wt)/2)`。**禁止**只凭面积最大或只看圆度不看形状类别。
  **5b. 浅色细线丝印圆（粉/浅红圈 + 白底，灰度主路径 0 候选时）**：**仍在同一 100×100 ROI 内**，**不得**扩大 `tp_work_roi`。**须**在 **`stdout`** 声明 **`step0c_path=faint_hsv_ring`** 后再用本条（与主路径二选一择优：**若主路径已有合格候选则不必走本条**）。流程：
     1. ROI **BGR → HSV**； **`cv2.inRange`** 抓浅红/粉丝印（起点示例：**H∈[0,12]∪[168,179]**、`cv2` 用 0–179；**S∈[12,255]**；**V∈[70,255]**）。若 mask 过稀，**仅在本 ROI 内**把 **S 下限降到 8–15** 或 **V 上限微降**重试 **一次**，并 **`stdout` 打印所用范围**。
     2. **`cv2.morphologyEx(..., MORPH_CLOSE)`**：核 **3×3 或 5×5**，**1～2 次**；再接 **≤3×3 `dilate`、iter≤1**，使细断圆环连成 **单连通** 前景。
     3. **`findContours`** 后过滤：**须**排除 **明显长条**（短边/长边 **< 0.55** 且 perimeter 很大者）。**细线圆环**允许略宽判据：**`circ ≥ 0.55`** **且** **`area/(π r²) ∈ [0.10, 1.12]`**（`r` = `minEnclosingCircle`），**且** 圆心距 **步骤 5 同一参考点** ≤ **28 px**（避免抓到远处走线弧）。**多候选**仍取距参考点 **最近** 者。
     4. **禁止**：不打印 **`faint_hsv_ring`** 就大幅放宽圆度乱套轮廓；**禁止**用本路径套 **矩形丝印块**（短长比过低且 `area/(πr²)` 偏离过大者应剔除）。
  6. 在 **`full`**（与步骤 1 同读的 BGR）上 **`cv2.circle(..., (0,255,0), thickness)`**，**`cv2.imwrite("debug/case10_assembly_drawing_tp_marked.png", full)`**。
  7. **`stdout`**：`tp_work_roi`（`wl,wt,wr,wb`）、**`work_roi_png`**、**`sx,sy,pw,ph`**、圆心半径及 **`step0c_path`**（**`gray_thresh`** 或 **`faint_hsv_ring`**）；可选 **`debug/case10_target_tp_opencv_debug.png`**（可在 debug 图上叠 ROI 矩形）。
  8. **0 候选**：**不得**改用更大 `tp_work_roi`；仅在 **步骤 5 与 5b** 内调参/各试一轮；**仍无**则 **`raise RuntimeError("case10 target TP: no pad contour after pdf-guided ROI")`**。

---

## Part B — 位号图：最大 IC（**底图 = 已带 TP 绿圈**；**主路径 = VLM `vlm_roi` → `work_roi` 内 OpenCV**）

**位号图不清晰时的「最大 IC」操作定义（防误判屏蔽罩）**：丝印/线稿 **对比度低、细节糊** 时，模型易把 **金属屏蔽罩、屏蔽壳、较大不规则导电盖** 当成「整页最大芯片」。**Part B 须按下述统一口径**：在全页（`tp_marked` 底图）上选 **`vlm_roi` 目标时，以「面积最大的矩形类封装器件」为「最大 IC」**——即 **外包络以矩形/准矩形为主** 的集成电路本体（典型 **QFP/QFN/LQFP 等**：中央塑封块 + **四面引脚/焊盘带** 在图上常呈 **矩形环或双层矩形**）。**不要将** **圆形/椭圆顶金属罩、单块不规则大金属外形（常见屏蔽结构）** 默认当作本规程的「最大 IC」；若整页 **可见多颗件**，在 **矩形封装** 子集里取 **包围盒面积最大** 者罩进 `vlm_roi`；若 **矩形 QFP 与更大块屏蔽罩并存**，**以该矩形 QFP 为准**，除非全页 **确实无** 更大矩形 IC（须在 **`spatial_description` / `rationale_zh` 写明**）。StepB3 QC 核对第 1 条时同此口径。

### StepB1 — VLM 与 hints
- **（可选）网格参考线，助 VLM 估准 `vlm_roi`**：位号图多为 **线稿 + 缺乏天然「块」纹理**，模型估 **像素级 bbox** 时容易 **整体平移或角点飘**；叠一层 **稀疏、浅色、半透明** 的 **等距网格**（仅视觉辅助）常能 **改善相对定位**——例如结合「大约在横向第几格、纵向第几格」与丝印推理，**但不保证**每次有效。**注意**：线 **过密/过粗** 会 **盖住丝印或与走线混淆**；网格 **只给 VLM 看图用**，**不得**作为 OpenCV 分割底图。**推荐**：`run_python` 复制 **`debug/case10_assembly_drawing_tp_marked.png`** → 按固定 **`step_px`**（如 **128**，或按宽高取约 **15～25 条**横纵线）画 **`cv2.line`**，**浅灰 + 与原图 alpha 混合**（或极低对比度），写出 **`debug/case10_assembly_drawing_tp_marked_grid.png`**（**W×H 与 `tp_marked` 必须一致**）。**`view_image`** 估框时 **可用带网格版**；写入 **`case10_assembly_vlm_hints.json` 的 `vlm_roi`** 仍是 **与原图相同的像素坐标**（可选记 **`grid_step_px`**、`vlm_visual_aid` 路径供复查）。
- **`view_image`**：**`debug/case10_assembly_drawing_tp_marked.png`** 或（若已生成）**`debug/case10_assembly_drawing_tp_marked_grid.png`**（至多一次，**二选一即可**）。**禁止**以无绿圈的 **`case10_assembly_drawing.png`** 作为 StepB1/2/3 的 annotate **`path`**。
- **中文三块** + **`case10_assembly_spatial_description.md`**（写明 **TP 绿圈** 与 IC 相对位置）。
- **`case10_assembly_vlm_hints.json`**：`image_wh` 与 **`tp_marked`** 一致；**`vlm_roi`** 盖住最大 IC（允许略紧，**`work_roi`** 会在 StepB2 **对称扩边**以包住引脚带）；**`selection_policy`=`"best_in_work_roi_max_package_area"`**（与 Part A 字面一致；**择优仅在 `work_roi` 内**）。**说明**：**`vlm_roi`** 仍是 **VLM 给出的候选窗**；**最终 IC 红框以 StepB2 OpenCV 输出为准**。
- **hints 与 OpenCV 交替（必守）**：**禁止**连续多步 **只** `save_text_file` **覆盖** **`case10_assembly_vlm_hints.json`** 却 **不**调用 **StepB2 `run_python`（读该 JSON 定框）**。**第一次**写出 hints 后 **下一工具调用必须是 StepB2**；**仅当** StepB3 **`QC_REVISE`**（正文写明偏框/漏脚等）时 **修订** JSON，且 **每写入新一版 hints，下一步必须立刻再跑一次 StepB2**，不得先连改多版 JSON、最后才跑一次 OpenCV；仍受 StepB3 **≤3 轮**修订。无 **`QC_REVISE`** 时不要反复覆盖 JSON「试手感」。
- **`vlm_roi` 尺度（必守）**：**目标**：`vlm_roi` **尽量**包住整颗 U501（塑封 + 四面引脚/焊盘带）。**若 VLM 初框偏紧**，StepB2 会 **对称扩成 `work_roi`**（与 Part A 同款 pad），**为 OpenCV 补出引脚带上下文**；仍 **禁止**懒到只框丝印字号大小。**`QC_REVISE`** 时仍可 **扩大或平移 `vlm_roi`**。**仍禁止**横跨多颗独立 IC 的整板糊框。

### StepB2 — OpenCV（**`work_roi` 内**；由 **`vlm_roi` 对称扩边**）
- **定框节奏**：与 StepB1 首条 **「hints 与 OpenCV 交替」**一致；若上一步刚改过 **`case10_assembly_vlm_hints.json`**，本步 **必须**执行 **`run_python`**，**禁止**跳过 OpenCV 继续改 JSON。
- **`vlm_roi` / `work_roi` 冻结**：**`vlm_roi` 必须且只能**来自 **`case10_assembly_vlm_hints.json`**（`json.load` → clamp）。**`work_roi` 只能**由 **下述公式**从 **该** `vlm_roi` **对称扩边**得到，与 **Part A Step2 第 2 步**相同：`rw = right-left`，`rh = bottom-top`，**`pad = max(64, int(0.20 * max(rw, rh)))`**，四边外扩后 **clamp** 到 **`[0,W]`×`[0,H]`**。**禁止**在 `run_python` 里手写、平移或另设 **`work_roi`**；不满意 **只改 JSON 中的 `vlm_roi`** 再跑。
- **分割窗口**：灰度阈值 / 形态学 / **`findContours` / 候选过滤** **仅限** **`work_roi` 子图** `img[wt:wb, wl:wr]`；**禁止**在未扩边的 **`vlm_roi`** 上单独跑主分割链（**可把 `vlm_roi`、`work_roi` 画进 debug 图**）。
- **形态学（位号图）**：**弱 dilate（≤3×3, iter≤1）**，防与邻件粘连；**禁止**照搬 Part A 的 **5×5 重膨胀** 整套到 **位号图**。
- **`run_python`**：**`json.load`** → **`cv2.imread`(`debug/case10_assembly_drawing_tp_marked.png`)**（**须为无网格原版**；**禁止**用 `_grid` 图做阈值）→ clamp **`vlm_roi`** → 构造 **`work_roi`** → 裁 **子图** → 阈值 → **弱 dilate** → **`findContours`** → 过滤噪声 → **最大封装面积**主轮廓（**禁止**几何中心锚点，规则同 Part A Step2 择优句）→ **`boundingRect` 映回全图**；**`stdout`** 打印 **`vlm_roi`、`work_roi`、最终 bbox**。**`debug/case10_assembly_opencv_debug.png`**（建议）：叠 **`vlm_roi` 细框**、**`work_roi` 粗虚线或异色框**、**最终红框**。
- **退化**：若主轮廓 **糊满 `work_roi`**（面积占比过高），**允许**与 Part A 同精神：**`work_roi` 四边各外扩 `pad2 = max(32, pad//2)`**（clamp）后 **同一规则重试一次**；仍退化则 **收紧 dilate/阈值** 或 **`QC_REVISE` 扩大 `vlm_roi`**。**全图 fallback** 须 **stdout 声明原因**。

### StepB3 — 红框标注（保留绿圈）
- **定稿导出顺序（硬约束，显然逻辑）**：凡是本轮最终被采用的 **`bbox`** 一旦确定（含 **`QC_REVISE`** 后 StepB2 给出的**新**框、或与首轮相同），**下一动必须是** **`annotate_image`**：`out_path` **只能**是 **`debug/case10_assembly_largest_ic_box.png`**，用**当前最终** `bbox` **覆盖**磁盘上旧图（**禁止**保留修正前的 PNG；**每次覆盖会清除** `debug/case10_stepb3_viewed_largest_ic_box.json`，**须**再 **`view_image`**）。**`view_image` 同一 PNG 之后**才允许 **`save_text_file` → `debug/case10_assembly_largest_ic.json`**（其中 **`bbox`** 与**刚写出**的 PNG 完全一致）。**禁止**只改 JSON / 只改 `bbox` 文本却**不**再跑一次 **`annotate_image`**；**禁止** JSON 与 PNG 来自不同轮次的红框。
- **工具顺序（硬约束）**：**`annotate_image`** → **`debug/case10_assembly_largest_ic_box.png`** → **下一动必须是 `view_image`（同一 PNG；运行时会写入 `debug/case10_stepb3_viewed_largest_ic_box.json`，`finish` 校验其与当前 PNG 的 mtime 一致；每次覆盖该 PNG 会清除 gate，须重新 view）** → 正文 **StepB3 清单核对** + **`QC_PASS` / `QC_REVISE`**。若 **`QC_REVISE`**：**改 hints → StepB2 → 再 `annotate_image` 覆盖 PNG → 再 `view_image`**，直至 **`QC_PASS`**。**仅当**本轮回合以 **`QC_PASS`（或 `QC_PASS_WITH_CAVEATS`）结束时**，才允许 **`save_text_file` → `debug/case10_assembly_largest_ic.json`** 与 **进入 Part A**。**禁止**「画完红框 → 直接写 JSON / 直接开实物图」跳过 QC；**禁止未看图**在正文写 **`QC_PASS`**。
- **`annotate_image`**：**`path`=`debug/case10_assembly_drawing_tp_marked.png`**，**`points`** 仅 IC 红框一项，**`out_path`=`debug/case10_assembly_largest_ic_box.png`**。
- **`view_image`（宣布 QC 前必做）**：**`debug/case10_assembly_largest_ic_box.png`**（每轮红框更新后 **至少一次**；首轮回合在 `annotate_image` 后 **立即**）。**在正文写 `QC_PASS` / `QC_REVISE` 之前**，须在图上逐项核对（**不可**未看图就默认通过）：
  1. **最大 IC（线稿位号图）**：红框是否套在**整页范围内、按矩形封装口径选出的最大那颗 IC**（见 Part B 段首「矩形封装 / 防屏蔽罩」操作定义），通常即 **最大 QFP/LQFP 类矩形本体 + 可见引脚带**，而不是 **更小邻近 IC**、**连接器**、**仅丝印字块**、也不是把 **圆形/不规则大屏蔽罩** 误当「最大芯片」。若肉眼可见 **页面上仍有更大的矩形封装未被红框覆盖** → **必须 `QC_REVISE`**，**禁止**写 **`QC_PASS`**。
  2. **完整性**：**塑封主体**与**四面可见的引脚/焊盘带**是否均在框内；任一侧**明显裁脚、只框到局部、或框重心明显落在错误器件上** → **必须 `QC_REVISE`**（写明偏哪一侧/套错哪颗），**禁止**为省事写 **`QC_PASS`**。
  3. 若需核对 **`vlm_roi` / `work_roi` 与红框关系**，**可** **`view_image`** **`debug/case10_assembly_opencv_debug.png`**（**整图至多再增加这一次**）；**更推荐**对 **`case10_assembly_largest_ic_box.png`** 用 **`crop_image`** 只裁 **IC 邻域** 后 `view_image`，以控制请求体积。
- **`QC_PASS`/`QC_REVISE`**（≤**3** 轮）：须与上图自检结论一致。**位号图 Part B（与 Part A 区分）**：**必须至少经历一次** **`QC_REVISE`** 闭环（改 hints → StepB2 → 再画红框 → 再 view）后再 **`QC_PASS`**；**禁止**首轮看图后直接 **`QC_PASS`** 且 **`qc_rounds_used==1`**（`finish` 契约要求 **`part_b_stepb3_qc.qc_rounds_used`≥**`2`**）。**实物图 Part A** 不设「必须 REVISE」轮数下限。**`QC_REVISE`**：改 **`case10_assembly_vlm_hints.json`** 中 **`vlm_roi`**（必要时 **`spatial_description`**）→ **下一工具必须是 StepB2 `run_python`** → **再 `annotate_image` 覆盖 **`debug/case10_assembly_largest_ic_box.png`**（新 bbox，必选）** → 再 **`view_image`** ……直至 **`QC_PASS`**。
- **`QC_PASS` 后**：**`save_text_file` → `debug/case10_assembly_largest_ic.json`**（合法 JSON），**`bbox`** 必须与**最后一次** **`annotate_image` 写入 PNG 的**红框 **完全一致**：**`[left, top, right, bottom]`** 整数、全图坐标（同 Part A 的 **`case10_largest_ic.json`** 字段风格）。**必须**同时写入 **`part_b_stepb3_qc`**（`finish` 会校验），示例（**含至少一轮 REVISE 后的终局**）：
  ```json
  "part_b_stepb3_qc": {
    "final_verdict": "QC_PASS",
    "viewed_largest_ic_box_png_before_final_json": true,
    "final_annotate_overwrote_png_immediately_before_json": true,
    "qc_rounds_used": 2,
    "whole_page_largest_package_checked_zh": "已对照整页：红框为最大QFP，未见更大封装漏选。"
  }
  ```
  - **`final_verdict`**：仅允许 **`QC_PASS`** 或 **`QC_PASS_WITH_CAVEATS`**（写入本 JSON 时**不得**仍为 **`QC_REVISE`**）。
  - **`viewed_largest_ic_box_png_before_final_json`**：必须 **`true`**（表示已 **`view_image`** **与最终 bbox 对应**的那版 `case10_assembly_largest_ic_box.png`）。
  - **`final_annotate_overwrote_png_immediately_before_json`**：必须 **`true`**（写入本 JSON 的**上一动**须为 **`annotate_image` → `case10_assembly_largest_ic_box.png`**，用**当前 JSON 内同一** `bbox` **覆盖**旧 PNG；**`QC_REVISE` 改框后不得省略**；**每一轮**定稿均 **禁止**只更新 JSON 不重画 PNG）。
  - **`whole_page_largest_package_checked_zh`**：一两句中文，确认已按上列第 1 条核对「整页最大封装」。
  - **`qc_rounds_used`**（**必填**，整数 **≥2**）：Part B StepB3 **annotate+view+裁决** 的轮数（**含**至少一次 **`QC_REVISE`**）；**Part A 无此下限**。
- **下一动**：开始 **Part A（实物图）**。可选：在 `progress/step_01B.md` 记录 **`case10_assembly_largest_ic_box.png`** 与 QC（**非** `finish` 唯一依赖）。

## Part A Step2 稳定参数经验（仅 Part A 实物图；可复用，优先单次 `run_python`）
- **`opencv_ballpark` 必须参与过滤**：用 **`package_short_side_px`**、**`aspect_ratio`**（如 QFP 约 **0.8–1.2**）去掉窄条、长条与异常大块，再在**剩余候选**中取 **`contourArea` 最大**；避免只靠面积选中**糊满 work_roi** 的伪轮廓。
- **阈值略严 + dilate 克制**：例如实物图 **`fixed_thresh_inv_dark` 55–70**、**3×3** 核、**`dilate` 迭代 0–1**，使塑封与引脚连成**一块**即可，**切勿**为过松阈值或过重膨胀把邻域焊成整片前景。
- **「Step13 式」深色 QFP**：在 **`work_roi` 子图**内可优先 **HSV 低 V**：`cv2.inRange(hsv, [0,0,0], [180,255,<hsv_upper_v>])`，典型 **`hsv_upper_v`≈55–70**；**5×5**、`dilate` 2、`erode` 1（详见原 case10 **`opencv_ballpark`** 字段）。
- **`stdout`** 打印候选 **面积、bbox、AR、占 work_roi 比例**；满意即 **`annotate_image`**，忌反复无目的改参。
- **糊满 work_roi**：先收紧 mask/过滤；**勿**首选扩 `work_roi`。

---

## Part A — 实物图流程（须在 **Part B `QC_PASS`** 之后执行）

### Step0 — 必读入后先做「横幅」工作底图（强制）
- 用 **`run_python`**（推荐）读 **`INPUT_PATHS["front_board_photo"]`**：`cv2.imread`。注意 OpenCV 的 **`shape` 顺序为 `(高, 宽)`**，与「宽高比较是否竖幅」时请用 **列宽 vs 行高** 一致判断。
- 若 **`宽 < 高`**（竖幅），则 **`cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)`** 得到 **横幅**（典型竖图转横后约 **4096×3072**）。  
  - 若 **`宽 ≥ 高`**（已是横幅），**不要**再旋转，仅 **拷贝像素** 写入工作文件即可。
- **`cv2.imwrite`** → **`debug/case10_board_landscape.png`**（**最终该文件满足 宽≥高**）。
- **`image_info`** / **`view_image`** 核对 `debug/case10_board_landscape.png` 的宽高。
- **自此以后**：说理、`bbox`、`annotate_image` 的 **`path`** **全部只能**针对 **`debug/case10_board_landscape.png`** 的像素坐标系。

### Step1 — VLM 理解与可执行 hints（必须先于 OpenCV）
- 在你已 **`view_image`** 过 **`debug/case10_board_landscape.png`** 之后完成本节。**禁止**在本节完成前调用 **`run_python`(OpenCV 定框)** 或 **`annotate_image`**。
- **中文叙述**（**三块**，写入正文 + **`debug/case10_spatial_description.md`**）：为何是最大 IC、板上位置、周围 landmarks。
- **必须用 `save_text_file` → `debug/case10_vlm_hints.json`（合法 JSON）**，字段 **至少**：
  - `coordinate_frame`: `"top-left_origin_x_right_y_down_pixels"`
  - `image_wh`: **`[width, height]`**，须与 **`image_info`(横幅)** 一致
  - **`vlm_roi`**: **`{ "left": int, "top": int, "right": int, "bottom": int }`** —— **盖住目标最大封装（含预估引脚/焊盘带）的宽松矩形**，可略松；**必须整数**、`right>left`、`bottom>top`、clamp 在图内
  - `opencv_ballpark`（可选但推荐）：如 **`include_leads`** 置 `true` 时倾向略松阈值或小核 **`dilate`（≤3×3，iter≤2）** 使塑封与引脚在 mask 上相连；外加 **`fixed_thresh_inv_dark`** / **`use_otsu`**、**封装短边像素区间**、`长宽比` 区间（按 **整块封装**估）。**仅实物图**可另加 **`prefer_hsv_dark_package`**、**`hsv_upper_v`**、**`morph_kernel_size`**（推荐 **5** 与 **5×5 + 膨胀2 + 腐蚀1** 配套）、**`morph_dilate_iter`** / **`morph_erode_iter`**（**勿**把整套 HSV+5×5 套用于位号图）
  - **`selection_policy`**：固定 **`"best_in_work_roi_max_package_area"`**（见 Step2：**只在 `work_roi` 内**筛轮廓，**不设锚点**，在合格候选里取 **包住整颗封装**意义下的 **最大面积**主轮廓）。
  - `rationale_zh`: 一两句中文，说明 roi 大致罩住哪颗芯片

### Step2 — OpenCV（**仅 `work_roi`**，管线固定如下，读 `vlm_hints.json`）
- **Part A 定框 `run_python` 次数（成功路径只一遍）**：对实物图 **`case10_board_landscape.png`**，**用于算芯片 bbox 的 Step2 `run_python` 默认整场任务只允许 1 次**：一次 `json.load` hints → `work_roi` 内分割 → 得到 bbox → 进入 **Step3 `annotate_image`**。**若 OpenCV 结果与 debug 红框已可接受（`QC_PASS` 或等价判断）**，**禁止**再 **`save_text_file` 覆盖 `case10_vlm_hints.json` 仅为重跑定框**、**禁止**再次调用 **定框用** Step2 `run_python`**。**仅当** 明确 **`QC_REVISE`**（正文写清问题：偏框/漏脚/糊满等）且未超过 Step3 **至多 3 轮**修订时，才允许 **改 `case10_vlm_hints.json` 后再来第二次（或第三次）** Step2；不得无说明地「再跑一版试试」。**禁止**在同一轮修订中 **连续多次**只 **`save_text_file` 覆盖 `case10_vlm_hints.json`** 而 **跳过** **`run_python`（Step2）**——每写入新一版 hints，**下一步应立刻**再跑 **定框** `run_python`，除非已达 3 轮上限须 `QC_PASS_WITH_CAVEATS`。
- **`vlm_roi` / `work_roi` 冻结来源（禁止脚本内改窗）**：OpenCV 用的 **`vlm_roi` 必须且只能**来自 **`debug/case10_vlm_hints.json`**（`json.load` → clamp）。**`work_roi` 只能**由本节公式从 **该** `vlm_roi` **对称扩边**得到。**禁止**在 `run_python` 里 **手写、`Refined`、平移、另设** `vlm_roi` / `work_roi`；**禁止**用「看一眼图」在代码里改数字代替 JSON。若红框不满意且处于 **`QC_REVISE` 回合**，**只能**修改 **磁盘上的 `case10_vlm_hints.json`** 后 **再跑一次** Step2（仍受上条「次数」约束）。
- **固定管线（实物图 Part A）**：**`vlm_roi`（clamp）→ 对称扩 `work_roi` → 仅在 `work_roi` 子图内灰度阈值 / mask（可形态学）→ `findContours` → 按面积与长宽比等过滤 → 在合格候选中取 **`contourArea`（或等价）最大**的主轮廓 **`boundingRect` → 映回全图 `[gx,gy,gx+gw,gy+gh]`**。**禁止**几何中心锚点；**禁止**在主路径上对最终 bbox **再按比例放大/膨胀**（如整体 ×1.05、×1.2、各边加固定像素等）。引脚/焊盘须靠 **略松阈值或小核 dilate 让 mask 连成一块** 体现在该 `boundingRect` 内，而不是事后扩框。
- **前置**：`**debug/case10_vlm_hints.json`** 已存在；**Windows 下** `open(..., encoding="utf-8")`，避免 hints 含中文注释时 **`UnicodeDecodeError`**。
- **几何约束**：阈值、形态学、`findContours`、连通域分析与候选 bbox **一律只在 `img[work_top:work_bottom, work_left:work_right]` 及其坐标回映射上进行**。**禁止**为「找更大的块」去读 **`work_roi` 外的像素**作为主要路径。
- **为何先要 `vlm_roi` + 扩边 `work_roi`**：VLM **`vlm_roi`** 指明「最大封装在哪一带」；**`work_roi`** 保证裁切内含 **整颗 QFP/QFN 四面引脚**。OpenCV **只负责在 `work_roi` 内部**把整块封装包住。
- **`run_python`** 必须：
  1. **`json.load`** 读 **`debug/case10_vlm_hints.json`**，取 **`vlm_roi`**，按 **`image_wh` 校验**并把 **`vlm_roi` 四条边 clamp** 到图内。
  2. **构造 `work_roi`（工作台，对称扩边）**：在 clamp 后的 `vlm_roi` 上，令 `rw = right-left`、`rh = bottom-top`，  
     `pad = max(64, int(0.20 * max(rw, rh)))`（**至少 64px**，约 **短边 ~20%**）；  
     `work_left`、`work_top`、`work_right`、`work_bottom` 同前式 **clamp** 到 **`[0,W]`×`[0,H]`**。  
     **只允许**对这一子图做后续图像算子；禁止省略扩边又用未扩边 `vlm_roi` 当唯一工作台。
  3. **二值前景应体现「本体+引脚/焊盘」**：在 `work_roi` 内默认 **灰度 `THRESH_BINARY_INV`** + **`opencv_ballpark`** **小核 `dilate`（≤3×3，iter≤2）**；**仅实物图**若 `prefer_hsv_dark_package` 为真，可改用 **HSV + `inRange`（低 V）**，并用 **`opencv_ballpark`** 指定的 **5×5 + dilate 2 + erode 1** 等（见上文「Step13 式」推荐）。使 **塑胶与周圈引脚在 mask 上连成单连通块**。**切勿**阈值过严仅剩黑塑胶中心小块。
  4. **`findContours`** 得候选（或最大连通域），按 **`opencv_ballpark` 的面积/长宽比**过滤 **明显不合理的噪声块**。对每个剩余候选 **`boundingRect`**，映射回全图 **`(gx, gy, gw, gh)`**。**`stdout` 打印前若干候选及其 `contourArea` / bbox 面积。**
  5. **择优（禁止锚点）**：**不得**引入 **`vlm_roi`、`work_roi` 中心、欧氏距离、IoU-with-vlm_roi 一类再加权**。在通过过滤的候选中，选取 **`contourArea`（或前景面积）最大**者为 **`best`**；若有并列，可取 **`boundingRect` 包围面积 `(gw*gh)` 更大**者。**此即主规则**。（若仅余一个巨大伪轮廓糊满工作台，由下条处理，而非改用锚点。）
  6. **退化检测（整块糊满工作台）**：令 `work_rw = work_right - work_left`、`work_rh = work_bottom - work_top`。若 **`best` 的 `gw*gh >= 0.82 * (work_rw * work_rh)`**：  
     **`print`** 说明 → **`work_roi` 四边各外扩 `pad2 = max(32, pad//2)`**（clamp）**在同一规则下重跑**；仍退化则 **收紧 dilate / 调高阈值一档**后再试一次，最后再 **声明** Fallback。
  7. **最终 bbox** 即上一步 **`boundingRect` 映回的全图矩形**。**允许**的唯一几何修正：**越界时对边坐标做 **clamp** 到 `[0,W]`×`[0,H]`**。  
     **`禁止`**：在已定出 bbox 之后再起 **`run_python`** 做 **中心对称放大、按比例加宽高、手写「包容引脚再扩一圈」** 等（**成功路径不得第二次定框**；例外仅 **`QC_REVISE` 且已改 hints**，见 Step2 首条）。同一轮内 **`annotate_image` 的 bbox** 必须与本轮采纳的 **OpenCV stdout 最终 bbox** 一致。
  8. **`debug/case10_opencv_debug.png`**：**`vlm_roi`**、**`work_roi`**、**最终 bbox（粗红）**；**最终红框必须与传给 `annotate_image` 的一致**。
- 若 **`work_roi` 内合格轮廓数量为 0**，容许 **`work_roi` 再扩一次（每边≤20% 当前边长）**或 **声明后全图 fallback**，**必须在 stdout 打印原因**。

### Step2 推荐 `run_python` 范例（仅 Part A；从 `case10_vlm_hints.json` 读几何，勿脚本内改 `vlm_roi`）
- **不得写死 ROI**：示范代码里 **没有任何 `vlm_roi = {...}` / `work_roi = [...]` 字面量**。**`vlm_roi` 四条边只能**来自 **`hints["vlm_roi"]`** 再 **clamp 到图内**；**`work_roi` 只能**由 **pad 公式**从该 `vlm_roi` 推出（`wl,wt,wr,wb`）。复制时 **禁止**夹带旧 run 里的整数框；改几何 **只能**改磁盘上的 **`case10_vlm_hints.json`**。
- 下例演示：**读 hints → clamp `vlm_roi` → 公式构造 `work_roi` →（可选）HSV 黑包或灰度反色 → 5×5 膨胀2+腐蚀1 或回退 3×3** → 按 **`package_short_side_px` / `aspect_ratio` / `min_contour_area`** 过滤 → 最大 `contourArea` → 调试图与 **`case10_opencv_result.json`**。**不要**用于 **Part B**。
- **勿用 `raise SystemExit`**：应使用 **`raise RuntimeError(...)`**，以便 `run_python` 正常返回失败并让 agent **修订 hints 或进入后续 Part**，而不会终止整场。
- ```python
  import cv2
  import json
  import os

  WORK = os.environ.get("WORKSPACE", ".")
  HINTS = os.path.join(WORK, "debug", "case10_vlm_hints.json")
  IMG = os.path.join(WORK, "debug", "case10_board_landscape.png")
  DEBUG = os.path.join(WORK, "debug", "case10_opencv_debug.png")
  OUT_JSON = os.path.join(WORK, "debug", "case10_opencv_result.json")

  with open(HINTS, "r", encoding="utf-8") as f:
      hints = json.load(f)
  bp = hints.get("opencv_ballpark") or {}

  img = cv2.imread(IMG)
  H, W = img.shape[:2]
  # vlm_roi: ONLY from JSON (never hardcode left/top/right/bottom here)
  vlm = hints["vlm_roi"]
  l = max(0, min(int(vlm["left"]), W))
  t = max(0, min(int(vlm["top"]), H))
  r = max(0, min(int(vlm["right"]), W))
  b = max(0, min(int(vlm["bottom"]), H))
  rw, rh = r - l, b - t
  pad = max(64, int(0.20 * max(rw, rh)))
  # work_roi: ONLY pad-expanded from clamped vlm_roi (never hardcode)
  wl, wt = max(0, l - pad), max(0, t - pad)
  wr, wb = min(W, r + pad), min(H, b + pad)
  work = img[wt:wb, wl:wr]
  ww, wh = wr - wl, wb - wt
  print("vlm_roi", [l, t, r, b], "work_roi", [wl, wt, wr, wb], "work", wh, "x", ww)

  use_hsv = bool(bp.get("prefer_hsv_dark_package", False))
  if use_hsv:
      hsv = cv2.cvtColor(work, cv2.COLOR_BGR2HSV)
      vu = int(bp.get("hsv_upper_v", 60))
      lo, hi = (0, 0, 0), (180, 255, max(1, vu))
      mask = cv2.inRange(hsv, lo, hi)
      ksz = int(bp.get("morph_kernel_size", 5))
      di, ei = int(bp.get("morph_dilate_iter", 2)), int(bp.get("morph_erode_iter", 1))
  else:
      gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
      tv = int(bp.get("fixed_thresh_inv_dark", 60))
      _, mask = cv2.threshold(gray, tv, 255, cv2.THRESH_BINARY_INV)
      ksz = int(bp.get("dilate_kernel", [3, 3])[0] if isinstance(bp.get("dilate_kernel"), list) else bp.get("morph_kernel_size", 3))
      di = int(bp.get("dilate_iter", bp.get("morph_dilate_iter", 1)))
      ei = int(bp.get("erode_iter", bp.get("morph_erode_iter", 0)))

  k = max(3, ksz | 1)
  ker = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
  mask = cv2.dilate(mask, ker, iterations=max(0, di))
  if ei > 0:
      mask = cv2.erode(mask, ker, iterations=ei)

  contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
  min_a = float(bp.get("min_contour_area", 10000))
  asp = bp.get("aspect_ratio")
  if isinstance(asp, (list, tuple)) and len(asp) == 2:
      ar_lo, ar_hi = float(asp[0]), float(asp[1])
  else:
      ar_lo, ar_hi = 0.0, 999.0
  ss = bp.get("package_short_side_px")
  if isinstance(ss, (list, tuple)) and len(ss) == 2:
      ss_lo, ss_hi = int(ss[0]), int(ss[1])
  else:
      ss_lo, ss_hi = 0, 99999
  cdn = []
  for cnt in contours:
      a = cv2.contourArea(cnt)
      if a < min_a:
          continue
      x, y, cw, ch = cv2.boundingRect(cnt)
      if ch <= 0:
          continue
      ar = cw / float(ch)
      short = min(cw, ch)
      if ar < ar_lo or ar > ar_hi or short < ss_lo or short > ss_hi:
          continue
      gx, gy = wl + x, wt + y
      cdn.append((a, [gx, gy, gx + cw, gy + ch], ar))
  cdn.sort(key=lambda z: z[0], reverse=True)
  for i, (a, box, ar) in enumerate(cdn[:6]):
      print("cand", i, "area", a, "bbox", box, "AR", round(ar, 3))
  work_area = ww * wh
  if not cdn:
      print("No candidate — relax opencv_ballpark / vlm_roi in case10_vlm_hints.json then retry (QC_REVISE path).")
      raise RuntimeError("case10: no contour passed filters in work_roi")
  best_a, final, _ = cdn[0]
  gxr, gyr, gxr2, gyr2 = final
  if (gxr2 - gxr) * (gyr2 - gyr) >= 0.82 * work_area:
      print("WARNING: best bbox fills work_roi; tighten mask or vlm_roi in JSON")

  dbg = img.copy()
  cv2.rectangle(dbg, (l, t), (r, b), (0, 255, 0), 2)
  cv2.rectangle(dbg, (wl, wt), (wr, wb), (255, 0, 0), 2)
  cv2.rectangle(dbg, (final[0], final[1]), (final[2], final[3]), (0, 0, 255), 4)
  cv2.imwrite(DEBUG, dbg)
  with open(OUT_JSON, "w", encoding="utf-8") as f:
      json.dump({"bbox": final, "work_roi": [wl, wt, wr, wb], "vlm_roi": [l, t, r, b]}, f, indent=2)
  print("final_bbox", final)
  ```
- 启用「Step13 式」时在 **`case10_vlm_hints.json`** 的 **`opencv_ballpark`** 中建议至少：`"prefer_hsv_dark_package": true`, `"hsv_upper_v": 60`, `"morph_kernel_size": 5`, `"morph_dilate_iter": 2`, `"morph_erode_iter": 1`（并与 **`aspect_ratio` / `package_short_side_px`** 一致）。

### Step3 — 标注成品图
- **`annotate_image`**：`path` = **`debug/case10_board_landscape.png`**，**`points` 仅此一个**：`{"bbox":[l,t,r,b], "color":"red", "width":4, "label":"largest_ic"}`（标签可自定），**`bbox` = Step2 OpenCV 最终矩形（仅可加图像边界 clamp）**；**`out_path`=`debug/case10_largest_ic_box.png`**。**禁止**在完成 Step2 后再用单独脚本按比例放大、`expand_factor`、「包引脚再扩一圈」等改 **`[l,t,r,b]`**。
- **写完** **`debug/case10_largest_ic_box.png`** 后，**必须 **`view_image`(`debug/case10_largest_ic_box.png`)**。  
- **自检**：**`QC_PASS`**：红框包住 **整块最大封装（塑胶 + 四面引脚/焊盘带）**，任一方向 **不得明显裁掉引脚**；**`QC_REVISE`**：写明问题 → 调 `vlm_roi`/阈值/dilate → 重算 → 再 annotate → 再 view。至多 **3 轮**；仍不过关则 **`QC_PASS_WITH_CAVEATS`** + `needs_human_review`。
- **`QC_PASS` 后的下一动**：**`save_text_file` → `debug/case10_largest_ic.json`**（**`bbox`** 与红框一致），然后 **Part C — 导出 `step02_*` + `board_tp_marked`**。**禁止**再覆盖 **`case10_vlm_hints.json`**、**禁止**再对实物图跑 **Step2 定框 `run_python`**。
- （可选）**`progress/step_01.md`**：记录 **`vlm_roi`** 摘录、OpenCV **最终 bbox**、是否用过 fallback、QC 结论。

---

## Part C — Step2 锚点图导出 + 跨图 TP 映射

**目的**：**`debug/step02_board_front_anchor.png`** = 实物 **IC 红框** 成品；**`debug/step02_locator_front_anchor.png`** = 位号图 **TP 绿圈 + IC 红框** 成品（与 **`case10_assembly_largest_ic_box.png`** 同内容复制）。**`debug/board_tp_marked.png`** = 在 **实物** `case10_largest_ic_box.png` 上，按与位号图 **IC 红框**对应的单应性，画出 **映射后的 TP 绿圈**（圆心/半径由脚本计算）。

### StepC0 — 实物 step02
- **`run_python`**：**`shutil.copyfile`** 或 **`cv2.imread/imwrite`**：**`case10_largest_ic_box.png`** → **`debug/step02_board_front_anchor.png`**。

### StepC1 — 位号 step02
- **`run_python`**：**`case10_assembly_largest_ic_box.png`** → **`debug/step02_locator_front_anchor.png`**（**不得**改分辨率；**禁止**再 `cv2.circle` 补画 TP，除非 **Part 0 缺失**且任务显式 fallback）。

### StepC2 — 红框对齐，实物上叠 TP 绿圈（`board_tp_marked`）
- **前提**：已存在 **`debug/case10_assembly_largest_ic_box.png`**（绿圈+红框）、**`debug/case10_largest_ic_box.png`**（红框）、**`debug/case10_assembly_largest_ic.json`** 与 **`debug/case10_largest_ic.json`**（各自 **`bbox`** 与对应红框一致）。
- **`run_python`**（单段即可，**cwd 为 workspace**）：
  1. `from pathlib import Path`
  2. `import sys`
  3. `sys.path.insert(0, str(Path.cwd().resolve().parent))`  —— 指向**仓库根目录**（`case10_board_tp_marked.py` 所在目录）
  4. `from case10_board_tp_marked import run_from_workspace`
  5. `run_from_workspace(Path.cwd())`
- **产出**：**`debug/board_tp_marked.png`**（**=** 实物底图上的红框 **+** 映射绿圈）；**`debug/case10_tp_board_mapping.json`**（单应性与圆参数，便于复查）。
- **几何说明**：以两张图上 **IC 红框四角**（同顺序：左上→右上→右下→左下）求 **`cv2.findHomography`**，将位号图检测到的 **目标 TP 绿圈**（圆心+半径，与 Part 0 一致）变换到实物坐标系；圆半径按两框宽高的 **几何平均缩放**。

### StepC3 — 自检
- **`view_image`**（节制）：**`step02_locator_front_anchor.png`**、**`step02_board_front_anchor.png`**、**`board_tp_marked.png`** 各 **至多一次**。（可选）**`progress/step_01C.md`** 记录复制与 QC。

---

## Part D — 默认：`case12_step02_opencv_ic_align`（整图 Step2 + OpenCV 双红框 IC 对齐）

**前提**：**Part C** 已产出 **`debug/step02_locator_front_anchor.png`**、**`debug/step02_board_front_anchor.png`**（两张图 **均有** 与规程一致的 **IC 红框** + 位号侧 **TP 绿圈**）。**本 Part 不**再走 Step3 红框匹配、双 ROI、Step5–7 候选链；**目标 TP 在板图上的像素**由 **case12 图对齐** 直接算出。

**契约**：**`debug/step03_mapping.json`** **至少**含 **`"mapping_method": "case12_step02_opencv_ic_align"`**（可无 `locator_box` / `u,v` 等旧键；`finish` 对该路径走独立门禁）。

### StepD1 — 位号 Step2 抽象图（OpenCV）
- **`run_python`**（仓库根须在 `sys.path`；`WORKSPACE` 为 agent 工作区）：
  1. `from pathlib import Path`
  2. `import os`
  3. `from case12_step02_graph import run_build_step02_locator_graph`
  4. `run_build_step02_locator_graph(Path(os.environ["WORKSPACE"]))`
- **产出**：**`debug/case12_step02_locator_graph.json`**、**`debug/case12_step02_locator_graph.png`**。

### StepD2 — 实物↔位号 IC 红框对齐（纯 OpenCV）
- **输入**：**`step02_*_anchor.png`** 上的 **HSV 红框**（与 legacy **`run_align_locator_graph_to_board_ic_bbox`** 一致）；**两图均需清晰红框**。
- **`run_python`**：
  1. `from case12_step02_graph import run_align_locator_graph_to_board_ic_bbox`
  2. `run_align_locator_graph_to_board_ic_bbox(Path(os.environ["WORKSPACE"]))`
- **或 CLI**：`python case12_step02_graph.py align-board --mode opencv-red`
- **产出**：**`debug/case12_board_points_aligned.json`**（**`source`** = **`opencv_ic_bbox_isotropic_align`**）、**`debug/case12_board_approx_overlay_opencv.png`**（整板 TP/ref 叠标，用于自检）。

### StepD3 — Step8 定稿 + 映射标签
- **`read_text_file`**：**`debug/case12_board_points_aligned.json`** → 取 **`board_roi_target_px_approx`** = **`[x, y]`**（**全板** **`step02_board_front_anchor.png`** 像素系）。
- **`annotate_image`**：**`path`** = **`debug/step02_board_front_anchor.png`**，在 **`[x,y]`** 处画 **红色** 整圆/十字（半径与 **`tp_green_radius_px`×`transform.s`** 相当或 **≥10px**），**`out_path`** = **`debug/step08_final_tp.png`**。
- **`save_text_file` → `debug/step08_result.json`**：至少 **`pixel`**：**`[x, y]`**（与上完全一致）；**`selected_id`** 建议 **`"case12_graph_align"`**；**`marker_radius`** 与 annotate 半径一致（供复查）。
- **`save_text_file` → `debug/step03_mapping.json`**（覆盖或新建）：**仅**填 **`mapping_method`**：**`"case12_step02_opencv_ic_align"`**（可加 **`notes`** 说明本路径跳过 Step4–7）。

### StepD4 — 结束
- **`finish`**：`answer.pixel` = **`[x, y]`**（与 **`step08_final_tp.png`** / **`step08_result.json`** 一致，±1px）。

### CLI 实验：`python -m agent run <task.yaml> --mode vlm_test`
- **不修改** **`task.yaml`**；首轮 **Task** 文本自动附带 **`vlm_test` 规程附录**。
- **与默认本节差异**：仍 **Part 0 → Part B**；**跳过 Part A（实物 IC 红框 annotate）**；**Part D** 用 **`mapping_method` = `case12_step02_vlm_ic_align`**，**依赖 VLM** 写 **`debug/case12_board_largest_ic_bbox_vlm.json`** 并由 **`run_align_locator_graph_to_board_ic_bbox_vlm`**（见 **`case12_step02_graph.py`**）算出 **`case12_board_points_aligned.json`**（**`source`=`vlm_ic_correspondence_isotropic_align`**）。**`finish` 门禁**见 **`agent/agent.py`**（**不要求** **`board_tp_marked.png`**）。
- **环境变量**：未传 CLI 时可用 **`VLM_AGENT_WORKFLOW_MODE=vlm_test`**（一般由 CLI 写入 **`Config.workflow_mode`**）。
- **工具门禁**：`workflow_mode=vlm_test` 时，运行时设置 **`VLM_AGENT_WORKFLOW_MODE`**，并由 **`annotate_image`** / **`save_text_file`** / **`run_python`（源码扫描）** / **`case12_step02_graph.run_align_locator_graph_to_board_ic_bbox`** **禁止**再产出 **`case10_largest_ic_*`**（Part A）或调用 legacy **OpenCV 实物 IC 红框对齐**，并 **禁止在实物底板 raster 上先期用 CV/脚本画 bbox 来定 IC**，以免把 **`case12_board_largest_ic_bbox_vlm.json`** 变成「借壳」——须走 **`run_align_locator_graph_to_board_ic_bbox_vlm`**，且 VLM JSON 中的数须来自 **看图推理**（不要用 OpenCV **先跑一次再抄数**）。

---

## Part D-alt — 备用：`case10_dual_roi_layout`（双 ROI + 路径 A / B + Step3–8）

**声明方式**：**`debug/step03_mapping.json`** 中 **`mapping_method`** = **`"case10_dual_roi_layout"`**（并含 Step3 所要求的 **`locator_box`, `board_box`, …**）。**勿**与本节默认的 **`case12_step02_opencv_ic_align`** 混用。

**目的**：在 Part C 先验之后，**同尺寸的** locator / board 裁块上 **优先试路径 A**：位号 ROI 上 **OpenCV 自动拣 2–3 个邻近 ref**（**`case10_dual_roi_locator_refs.json` + `step04_locator_roi_refs.png`**），给出 **相对 tp 的距离与方位**；VLM 再据 **`step04_roi_crop`** 给出 **`board_roi_target_px_approx`** 与匹配的 **`board_roi_reference_approx`**；**`step04_dual_roi_approx_only.png`** 叠 **tp + 多 ref**（无焊盘检测候选）做 **视觉自检**（必要时 **一次**改点）；**OpenCV snap** **仅**对 **目标 tp** 落到真实焊盘圆心。**若 snap / QC 失败** → 可选 **编号候选图** 或 **路径 B**。**取代**旧版 **`run_candidate_pipeline`** 默认链。
**权重（路径 B）**：**VLM 规律 / `target_cell` > 候选几何落格**；prior 仅 **平局/自检**。**路径 A**：**近似点**须经 **叠图自检**；**最终几何**以 **snap 后 refined** 为准（主路径）；**fallback** 时可用 **VLM 在编号图上选 id**。

**契约**：**`debug/step03_mapping.json`** 必须含 **`"mapping_method": "case10_dual_roi_layout"`**；其余键 **`locator_box`, `board_box`, `tp_locator_center`, `u`, `v`, `tp_prior_board`** 同 **`skills_doc`** Step3。

### StepD3 — 全图先验 + 映射 JSON
- **输入**：**`debug/step02_locator_front_anchor.png`**、**`debug/step02_board_front_anchor.png`**；位号 **绿心** 取 **`case10_assembly_drawing_tp_marked.png`** 上 Part0 圆心（或 **`case10_tp_board_mapping.json`**）。
- **优先** **`run_step3_mapping`**（或等效 **`run_python`**：HSV 红框 + 绿点 + `u,v` 标量公式），在 **`case10_board_landscape.png`** 上得 **`tp_prior_board`**。
- **写出**：**`debug/step03_mapping.json`**（**必含** **`mapping_method`**）；**`debug/step03_prior_on_board.png`**。
- （可选）**`progress/step_03.md`**：坐标系说明；或仅在 **`step03_mapping.json`** / stdout 中保留关键信息即可。

### StepD4 — **双 ROI**（**与 Part 0 的 100×100 无关**）
- **Part 0** 的 **`tp_work_roi`** 仍为 **100×100**（`half=50`），仅服务 PDF 粗定位与绿圈拟合；**不得**把该尺寸套用到本步。
- **Board**：以 **`tp_prior_board`=`(px,py)`** 为锚 → **`debug/step04_roi_crop.png`**：**默认名义 `300×300` px**（**`half=150`**：`[px-150, py-150, px+150, py+150]`），**clamp** 到整图内；若贴边导致宽高略小于 300，以实际像素为准（可在 JSON / stdout 记录）。
- **Locator**：**`case10_assembly_drawing_tp_marked.png`** 上 **TP 绿心** **`(gx,gy)`** → **`debug/step04_locator_roi_crop.png`**：**`H×W` 与 board ROI 逐像素相同**（先 **`cv2.imread` 读 board crop 的 `shape`**，再对位号图用 **同一 w,h** 以 **`(gx,gy)`** 为中心 **clamp** 裁切）。
- **裁块自检**：若阵列、邻件或一整组 TP **被裁断**（仅露一半），VLM 须 **写明「局部不全」**；Agent **允许**将 **board / locator 同步增大** 同一对 **`half`**（如 `150→200`）**重裁两 ROI**，**仍须同 WxH**。

### StepD4.0 — **位号 ROI 多锚点参考（路径 A 强制）**
- **时机**：**`debug/step04_locator_roi_crop.png`** 已落盘、且与 **`debug/step04_roi_crop.png`** **同 WxH** 之后；**在** 首次为路径 A 编写 **`case10_tp_dual_roi_direct_vlm.json`** **之前**。**不得**跳过本步仅靠裸 **`step04_locator_roi_crop`** 让 VLM 单点猜 **board** approx（除非脚本明确写出 **0 个** `references`，须在后续 **`confidence`** 中体现并倾向路径 B）。
- **`run_python`**：**`case10_dual_roi_refine.run_build_locator_refs_from_workspace(Path.cwd())`**
- **产出**：**`debug/case10_dual_roi_locator_refs.json`**（**`schema_version`：2**；**`tp_center_roi`**、**`references[]`**：每项 **`ref_id`**（`ref_1`…）、**`kind`**（`ic_rect` / `pad_circle`）、**`center_roi` / `bbox_roi`、相对 tp 的 `dist_to_tp_px`、`angle_deg_atan2_dy_dx`**、**`graph_edges`**（仅 tp→ref）、**`pairwise_roi`**：**`tp` 与全部 ref 两两一组**（**节点按 tp → ref_1 → ref_2… 排序**，每条边 **`a`** 在 **`b`** 前）的 **`dist_px`、矢量 `dx_a_to_b` / `dy_a_to_b`、`angle_deg_atan2_dy_dx`**，形成**刚性图**，便于在实物 ROI 上任定两点即可校验/推算其余锚点）+ **`debug/step04_locator_roi_refs.png`**（与裁块同尺寸：**tp 标记、ref 彩色框/圈、tp→ref 彩色连线、ref 之间浅灰连线、`ref_id` 标签**）。
- **说明**：OpenCV 侧 **只做几何与可视化**；**语义对应**（哪颗件 ↔ 实物上哪颗）由 VLM 结合 **`step04_roi_crop.png`** 完成。**Snap** 仍 **只**对 **目标 TP** 使用 **`board_roi_target_px_approx`**。

- （可选）**`progress/step_04.md`**：**同时**写两文件路径、**各自 bbox**、**名义/实际 WxH**、**同尺寸**声明。

### 位号图 ROI → **VLM 固定任务**（**StepD4.5 / StepD5 共用**；先看全图再谈绿圈）
对 **`step04_locator_roi_crop.png`**，VLM **必须按顺序**完成（可写在同一 JSON 的多字段里，**禁止**跳过前几步直接猜绿圈）：
1. **器件**：在本裁图内 **尽可能列出** 能辨认的 **元件/封装/连接器/孔位**（可据丝印 **Uxxx/Jxxx** 等），说明 **各自在 ROI 内的大致方位**（左/右/上/下/角）。
2. **测试点**：列出裁图内 **所有能看到的 TP/测试焊盘**（丝印 **TPxxx** 或圆盘阵列），**逐组**说明 **个数、走向、是否整齐网格**。
3. **排列**：用**自然语言 + 方向**描述阵列（例如「两竖列、左列自下而上编号…」「与某 IC 相邻的一侧」）；若只拍到阵列的一部分，**明确写「仅局部可见、可能还有被裁掉的点」**。
4. **绿圈（目标点）**：在 **以上完整上下文** 下，说明 **绿色圆圈** 落在 **哪一组 TP/哪一列哪一行/从上或下数第几个**，以及与 **最近器件或丝印** 的相对方位。
- **禁止**：在**未交代**本 ROI 内器件与 TP 全貌（力所能及范围内）的情况下，只写一句「第几列第几个」；**禁止**把看不清说成看清——看不清须 **`confidence` 降低** 并倾向 **路径 B / 扩大 ROI**。

### StepD4.5 — **路径 A｜双 ROI 直接对照**（**先试**；在 StepD5 **规律法** 之前）
- **思想**：两裁块 **仅保证同 WxH**，便于 **并排对照**；**位号 ROI** 以 **绿心** 为锚、**实物 ROI** 以 **Step3 先验** 为锚，**二者内容不是同一像素坐标系，也不能假设「中心互相对齐」**。**位号侧**须先完成 **「位号图 ROI → VLM 固定任务」**；再在 **实物 ROI** 上找 **与位号布局同构** 的结构，给出 **板 ROI 内** 近似点；**StepD4.5B** 先用 **清晰叠加图** 让 VLM **核对 approx**，**至多修正一次**，再 **finalize**（默认 scaled 半径，可选 snap）。
- **锚点图结构冻结（路径 A；`step04_locator_roi_refs` → `approx_only`）**：
  - **`debug/case10_dual_roi_locator_refs.json`** 与 **`debug/step04_locator_roi_refs.png`** 定义 **已由 OpenCV 固定的抽象图**：节点 = **目标 TP（绿圈质心，`tp_center_roi`）+ 每个 `ref_*`**；**`pairwise_roi`** 为上述节点在 **位号 ROI 像素** 下的 **两两边**（距离与 `dx/dy`/角）；**`graph_edges`** 为 **tp→ref** 星形。**该图的结构（谁与谁相邻、哪条边多长/朝哪）是输入契约，不是 VLM 可改写的设计稿。**
  - **VLM 写 `case10_tp_dual_roi_direct_vlm.json`、并最终由脚本绘制 `step04_dual_roi_approx_only.png` 时**：
    - **`board_roi_reference_approx`**：**必须**与 **`references[]`** **同 `ref_id` 集合、同条数、一一对应**（不得省略、不得新增脚本未输出的 `ref_id`、不得改名）。
    - **禁止**：把位号上 **`ref_i`** 对应的器件类型/角色 **换配** 到实物上 **另一家 `ref_j`**（**锚点换位 / 语义串台**）；禁止仅凭「更像」**重写**锚点编号而不 **重跑 StepD4.0**。
    - **允许**：在实物 ROI 内用 **同一套 `ref_id` 标签**，通过 **整体平移 + 近似均匀缩放 + 少量像素噪声**，使板上各点之间的 **相对几何** 与 **`pairwise_roi`** **一致或可解释地接近**；**`board_roi_target_px_approx`**（TP）须与 **`tp_center_roi`** 在位号侧相对各 **`ref_*`** 的 **方位关系同构**。
    - **若实物 ROI 无法支持上述同构**（裁切不足、遮挡、与位号侧拓扑矛盾）：须 **`board_roi_target_px_approx`=null**（并处理 `board_roi_reference_approx` 为一致态或省略规则见路径 B）、**降低 `confidence`**、**`use_fallback_pattern_path`=true** 或 **同步扩大 board/locator ROI 后重跑 StepD4 + StepD4.0**，**禁止**为省事 **拆掉或篡改** 锚点图拓扑来写一个「看似接近」的 approx。
  - **`comparison_zh`**：**须逐个点名** 每个 **`ref_id`** 在 **`step04_roi_crop.png`** 上对应 **哪颗/哪类** 器件，并声明与 **`step04_locator_roi_refs.png`** 上 **同色/同标签框** 为 **同一角色**（不得只描述 TP 而不锁 ref）。
- **`board_roi_target_px_approx` 的推理约束（契约级）**：
  - **必须**：**只依据** 两幅 ROI 里 **能看到的器件 / TP / 孔 / 连接器** 的 **相对排布**，在实物图上指认「与位号绿圈所在那一格 **同构** 」的焊盘，再估计其圆心在 **board ROI 像素** 下的 `(cx,cy)`；`comparison_zh` 须写清 **左/中/右列、与邻件的相对方位** 等 **可核对** 证据链。
  - **禁止**：以 **「ROI 裁切以先验为中心 → 先验落在 ROI 几何中心附近 → 故目标必在 ROI 中心 / 中心列中点 / 正中焊盘」** 等 **几何对称或中心法则** 猜点（**两 ROI 锚点不同，严禁当叠图对齐**）。
  - **禁止**：**无** 两图结构互证时，用 **「中间列中间那个」**、**「看起来在正中间」** 等 **含糊对称** 代替 **行列/邻件** 叙述。
  - 若仅靠中心推断、**无法在实物 ROI 中指认与位号侧 **一致** 的阵列结构**，须 **`board_roi_target_px_approx`=null**、**降低 `confidence`**、**`use_fallback_pattern_path`=true**。
- **`view_image`**：**`step04_locator_roi_refs.png`**（**路径 A 必须**，含 OpenCV **`ref_*` 与连线**）与 **`step04_roi_crop.png`** **各一次**（小图；勿换全板）。若须核对丝印可读性，可 **额外最多一次** 裸 **`step04_locator_roi_crop.png`**（节制）。
- **`debug/case10_tp_dual_roi_direct_vlm.json`**（**每场必写**，即使将走路径 B；记录对照结论与是否 fallback）：
  - **`locator_roi_inventory_zh`（必填）**：对应上节 **步骤 1–3** 的 **详细书面结果**（器件列表 + 所有可见 TP + 排列），**写在** `comparison_zh` **之前**的逻辑顺序里；可拆分为子串但必须 **齐全**。须 **点名** **`case10_dual_roi_locator_refs.json`** 里各 **`ref_id`** 分别对应 ROI 内哪类器件/焊盘（与彩色框一致）。
  - **`green_circle_relative_zh`（必填）**：对应上节 **步骤 4**——仅基于位号 ROI，绿圈在布局中的 **相对位置**（列/行/序号 + 参照物）。
  - **`comparison_zh`**：在 **位号侧已描述清楚** 的前提下，写 **实物 ROI** 上与上述 **哪一组结构同构**（列数、邻件、走向是否一致），**如何唯一对应到** 目标焊盘；**不得**仅用「先验/中心」字样替代结构描述。
  - **`board_approx_basis_zh`（建议）**：**一句人话**说明 **`board_roi_target_px_approx` 纯粹由哪两条结构证据推出**（便于复查是否触犯禁止项）。
  - **`board_roi_target_px_approx`**: `[cx, cy]` — **仅限 board ROI 内** 的 **近似** 圆心（可偏离）；须符合上文约束；否则 **`null`** 并 **倾向** `use_fallback_pattern_path: true`。
  - **`board_roi_reference_approx`（路径 A；当 `locator_refs` 非空时必填）**：`[{"ref_id":"ref_1","center_px":[x,y]}, ...]`，**`ref_id` 与 `case10_dual_roi_locator_refs.json` 一致**，**board ROI 像素**。VLM 应同时复用 **`graph_edges`** 与 **`pairwise_roi`**：在板上标出的各 **`center_px`** 之间，**欧氏距离与相对矢量**应与位号侧 **`pairwise_roi`** 一致（容许小偏差）；用于**交叉验算**，避免只对准 tp—某一 ref 而整体拓扑错位。
  - **`confidence`**: 0–1（**裁切不全 / 描述含糊**须压低）。
  - **`use_fallback_pattern_path`**: **bool**；规则建议：**`confidence < 0.55`** 或 **无可靠 `board_roi_target_px_approx`** 或 VLM **明确歧义** → **`true`**，**必须**继续完整 **StepD5–D7**；否则可先走 **StepD4.5B** 再定夺。
  - **`geom_refine`**: **finalize** 参数。**默认**脚本以 VLM **approx 为圆心**，用 **位号/实物锚点图相对尺度** 推算 **`r_vis`**（`tp_green_radius_px`×scale），**不再**做 OpenCV 圆吸附。若需 **传统 snap**，在 JSON 中设 **`"mode": "snap_nearest_in_search_radius"`**；此时 **`search_radius_px`** 为距 **最终** `board_roi_target_px_approx` 的搜索半径；可随 ROI **half** 略增。其它字段（`min_circularity`、`merge_dup_px` 等）**仅在 snap 模式**下使用。示例：`{ "mode": "vlm_approx_scaled_radius", "search_radius_px": 28, "min_circularity": 0.55, ... }` 或省略 **`mode`** 即走默认（scaled）；snap 时用 **`"mode": "snap_nearest_in_search_radius"`**。

### StepD4.5B — **仅 approx 叠图 + 看图自检 +（至多一次）改点 + finalize（主路径）**
- **默认**：**不要**把 **满屏编号候选** 当作主流程必看图（易乱）；改用 **`step04_dual_roi_approx_only.png`**。
- **B1 — 叠图（`run_python`）**：读 **`step04_roi_crop.png`** + **`case10_tp_dual_roi_direct_vlm.json`**，在 **板 ROI 拷贝** 上绘制 **`board_roi_target_px_approx`**（品红 **tp** 十字）+ **`board_roi_reference_approx`** 各 **ref**（异色十字 + **tp→ref 浅色连线**，**ref–ref 浅灰连线**（与位号侧 `step04_locator_roi_refs` 一致），`clamp` 在图内）。**不得**叠画 OpenCV **焊盘检测**候选圆。推荐 **`case10_dual_roi_refine.run_write_approx_overlay_from_workspace(Path.cwd())`** → **`debug/step04_dual_roi_approx_only.png`**。若需同时看一眼搜索半径，可 **`draw_search_radius=True`**（淡圈，**可选**；**仅**在 **`geom_refine.mode`** 为 **snap** 时有意义）。
- **B2 — 看图自检（`view_image`）**：**必须** **`view_image`** **`debug/step04_dual_roi_approx_only.png`**。对照 **`step04_locator_roi_refs.png`**、**`case10_dual_roi_locator_refs.json`**、**中文字段**，判断 **tp 与各 ref 的相对几何** 是否与位号侧 **一致**；**tp 十字** 须落在 **与绿圈同构** 的目标焊盘（容许 **小幅像素误差**；**明显错格/错列**须走 B3）。
- **B3 — 至多一次修正 approx**：若自检不通过，**仅允许一次** **`save_text_file` 覆盖** **`case10_tp_dual_roi_direct_vlm.json`** 中的 **`board_roi_target_px_approx`** 与（若已写）**`board_roi_reference_approx`**（整数 ROI 像素），并写入 **`approx_revision_zh`** 说明 **依据**。修正 **仅限坐标微调** 以满足 **与 `pairwise_roi` 同构**；**不得**在此步 **改 `ref_id` 集合、换锚语义或改拓扑**。然后 **重复 B1**；**可再 `view_image` 新叠图至多一次**。**禁止**无原因反复改坐标蹭步数。
- **B4 — finalize（`run_python`）**：**默认**：圆心 = **（最终）**`board_roi_target_px_approx`**；半径 = **`tp_green_radius_px`**（来自 `case10_dual_roi_locator_refs.json`）× **结构比例 scale**（优先 **ref–ref**：板上两 ref 间距 ÷ 位号侧 `pairwise_roi` 同对间距，取 **中位数**；否则 **tp–ref** 中位数）。写出 **`debug/case10_tp_dual_roi_direct_refined.json`** + **`debug/step04_dual_roi_direct_snap.png`**（品红 approx、绿色圆；文件名沿用）。推荐 **`case10_dual_roi_refine.run_snap_nearest_from_workspace(Path.cwd())`** 或 **`run_dual_roi_finalize_from_workspace`**（等价）。**`stdout`**：**PASS/FAIL**、**scale**、**r_vis**；**`selection_method`** 为 **`vlm_approx_scaled_radius`**。
- **可选 OpenCV snap**：在 **`case10_tp_dual_roi_direct_vlm.json`** 设 **`geom_refine.mode`** 为 **`snap_nearest_in_search_radius`** 后重跑 B4：**高召回** 圆候选 → 距 approx ≤ **`search_radius_px`** 且满足 **`min_circularity`** 者中 **最近**（**并列圆度更高优先**）→ 仍写同一 refined / PNG（吸附线仍画 approx→snap 点）。
- **失败与歧义**：**`qc_direct_path==FAIL`**（例如 **无足够 ref 对推算 scale**）时，**允许** **一次** 补全 **`board_roi_reference_approx`** 或略调 **`geom_refine`**（或改走 **snap 模式**）后重跑 **B4**；仍失败 → **StepD4.5C** 或 **StepD5–D7（路径 B）**。
- **若 `qc_direct_path==PASS`**：按 refined 写入 **`step05_candidates.json`** / **`step57_*`**，**`selection_rationale_zh`** 写明 **`approx_overlay_qc + scaled_radius`**（若用了 snap 则写 **snap**）。

### StepD4.5C — **可选 fallback：编号候选图 + VLM 选号（snap 失败 / 多候选 tie 难解时）**
- **`run_python`**： **`case10_dual_roi_refine.run_enumerate_from_workspace(Path.cwd())`** → **`case10_tp_dual_roi_direct_candidates.json`** + **`step04_dual_roi_direct_candidates.png`**。
- **`view_image`**：**`debug/step04_dual_roi_direct_candidates.png`**（**必须**）。
- **`save_text_file` → `debug/case10_tp_dual_roi_vlm_pick.json`**（`winner_candidate_id` + `confirm_zh`）；**`case10_dual_roi_refine.run_apply_pick_from_workspace(Path.cwd())`** → refined。

### StepD5 — **路径 B｜**位号 ROI **全貌** + 目标规律（**路径 A 未采用或失败时必做**）
- **`view_image`(`step04_locator_roi_crop.png`)**（**路径 B 必须**）；若 **StepD4.5** 已对同图 `view_image`，**勿重复上传**以利省 token，但 **`case10_tp_roi_layout_vlm.json` 仍须独立落盘**。
- **`debug/case10_tp_roi_layout_vlm.json`**——**路径 B** 专用：**只基于位号 ROI** ；须 **先**遵守上文 **「位号图 ROI → VLM 固定任务」**（器件 → TP → 排列 → **最后**绿圈相对位），再写结构化字段。**实物坐标**归 **路径 A** 的 **`case10_tp_dual_roi_direct_vlm.json`**，勿在本文件写全板像素。
  - **`locator_roi_inventory_zh`（必填）**：与 StepD4.5 同要求——**器件 + 全部可见 TP + 排列** 的**详细**叙述（可与路径 A JSON **复用同一段文字**以减少漂移）。
  - **`green_circle_relative_zh`（必填）**：绿圈在 **上述布局** 中的相对位置。
  - **`full_roi_layout_zh`（必填）**：在 `locator_roi_inventory_zh` 基础上的 **结构化摘要**（仍可写邻件/铜皮/隔离带等），须与 **`locator_roi_inventory_zh`** 一致。
  - **`layout_summary_zh`**：**一句话**收束，如「目标绿圈为右列自上往下第 3 个 TP 焊盘」。
  - **`pattern`**：`{ "kind": "grid"|"linear", "rows": N, "cols": M, "ordering": "row_major"|"col_major"|"custom", "target": { "row": r, "col": c } }`；非网格则 **`ordinal`**（0/1-based 须在 **`axes_desc_zh`** 写明）。**须与 `locator_roi_inventory_zh` / `green_circle_relative_zh` 不自相矛盾**。
  - **`axes_desc_zh`**：行/列正方向与 ROI 像素 **x 右、y 下** 的对应；若存在多组阵列，写明 **以哪一组为 `pattern` 的计数参照**。
  - **`visual_anchors_zh`**：ROI 内 **多个** 稳定参照（不仅目标旁一点）：邻件轮廓角、丝印、走线拐点等。
  - **`confidence`**：0–1。
- **路径 B**：**禁止**在本 JSON 里写 **实物板** 全图坐标；实物落点由 StepD7 + pattern 或已由路径 A 解决。

### StepD6 — 可执行 hints（VLM → OpenCV 契约）
- **`debug/case10_tp_roi_layout_hints.json`**——须与 StepD5 一致、且机器可读，建议：
  - **`roi`**: `{ "board": {x,y,w,h}, "locator": {x,y,w,h} }`（与两 PNG 裁切一致；**w,h 相同**）。
  - **`full_roi_layout_zh`**: 可与 VLM JSON 同文或摘要，供脚本 `stdout` / 复查。
  - **`grid`**（若适用）: `{ "n_rows", "n_cols", "order", "row_axis": "down"|"up", "col_axis": "right"|"left" }`；**`target_cell`**: `{ "row", "col" }`。**OpenCV 选点必须以该字段 + `pattern` 为最高优先级**，不得以 prior 覆盖。
  - **`detector`**: `{ "method", "min_circularity", "max_circularity", "min_area", "max_area", "blur_ksize", "dual_threshold": bool, "merge_dup_px": int, "notes_zh" }`——**默认倾向「高召回」**：阈值/面积范围 **略宽**、允许 **`dual_threshold`** 双路二值合并候选、近重复圆 **`merge_dup_px` 内合并**；宁可 **`candidates` 偏多**，再由 **`pattern` 落格** 去掉干扰。**禁止**为「干净」而过早收紧到 true target 不在集合内。
  - **`refine`**: `{ "prior_board_full": [px,py], "roi_origin_board": [ox,oy], "snap_max_px": d, "prior_role": "sanity_or_tie_break" }`——**`prior_role` 固定语义**：先验 **仅**用于 **pattern 已指明但几何上两候选难以区分** 时的平局、或 **选出后与 prior 偏差过大** 时在 **`step05_candidates.json` / QC 记录** 声明告警；**禁止**把 `snap_max_px` 当作「只在 prior 附近搜圆」的硬窗口从而漏掉 true pad。
  - **`global_output`**: 选中圆心后 **`gx = ox + cx_roi`, `gy = oy + cy_roi`** 写入 **`step05_candidates.json`**。

### StepD7 — OpenCV（board ROI）：**路径 B｜先捞全，再按 VLM 规律选**（路径 A 成功时可跳过本节选点逻辑，仅补流水）
- **若路径 A 已采纳**（StepD4.5B **`PASS`**）：可 **不**再按 `pattern` 重选；须保证 **`step05_candidates.json`** / **`step57_*`** 完整，**`selection_rationale_zh`** 注明 **approx_overlay_qc + snap**；若走 **StepD4.5C**，注明 **vlm_pick_on_candidates** / **`winner_candidate_id`**。
- **否则（路径 B）** — **`run_python`**：读 **`case10_tp_roi_layout_hints.json`** + **`step04_roi_crop.png`** → **高召回** 检出 ROI 内 **尽量完整的疑似圆焊盘列表**（可多方法并行再 **并集/去重**）→ 将候选 **按 `grid` 轴方向聚类/排序** 成与位号图一致的 **行×列（或序）索引** → **用 `target_cell` / `pattern` 指定格点映射到唯一候选**得 **`winner`** → 写出 **`debug/step05_candidates.json`**（建议 **`candidates[]`** 含 **`id`, ROI `cx,cy`, `r_vis`, `circularity`, `dist_to_prior`, `pattern_row`, `pattern_col`（若可指派）**；**`winner_id`**, **`winner_global`**, **`winner_r_vis`**, **`selection_rationale_zh`**：**路径 B** 须说明 **pattern + prior tie-break**；**路径 A（主路径）** 须说明 **approx_overlay_qc + snap**；**路径 A（D4.5C fallback）** 须说明 **vlm_pick + candidates**）+ **`debug/step57_candidates_scored.png`**（**全部**候选编号 + winner 高亮；**若存在** **`case10_tp_dual_roi_direct_vlm.json`**，**须叠画** **`board_roi_target_px_approx`**（品红十字 **`VLM approx`**），或调用根目录 **`case10_draw_step57.run_from_workspace`**）。
- **选点逻辑（须与 `step05_candidates.json` 一致）**：**(1)** OpenCV **以召回为主**，确保 **真点极大概率 ∈ candidates**（若过少须降阈值/加一路检测并在 JSON / stdout 说明）。**(2)** **以 VLM `grid`+`target_cell`（或 ordinal）为主** 从候选中取用；**禁止**仅用「距 prior 最近」或「`snap_max_px` 内唯一」作为主规则——**除非** 已写明 **pattern 无法落格** 且已尝试放宽检测。**(3)** **prior**：仅在 **pattern 落格歧义** 时参与 **tie-break**，或与 winner 距离超 `snap_max_px` 时 **QC 告警仍保留 pattern 选择** 并记录原因。
- **禁止**默认 **`run_candidate_pipeline`**；失败则在 stdout / **`step05_candidates.json` / 可选 `progress/step_06.md`** 声明后再议。

### StepD8 — 全图标注 + **`finish`（交付）**
- **`annotate_image`** → **`debug/step08_final_tp.png`**（**全板** **`case10_board_landscape.png`** 坐标系下红圈标记）；写出 **`debug/step08_result.json`**（须含与所选候选一致的 **`pixel` [x,y]**、`selected_id`、`marker_radius` 等）。
- **`finish`**：`answer.pixel` = **目标 TP 在实物整板图上的像素中心 [x, y]**（与 **`step08_final_tp.png`** / **`step08_result.json`** 一致，±1px）；`needs_user_help=true` 时可省略像素但须说明原因。
- **`progress/step_05.md`** 等 Markdown **不**再作为 `finish` 硬性条件。

---

## 必做工具与产物（顺序概要）

### Part 0 — 原理图→TP + PDF → 绿圈（位号 PNG）
1. **`view_image`（原理图）** + **`save_text_file`** → **`case10_signal_to_tp.json`**（**`tp_id_or_ref`**）。
2. **`search_pdf_text`**（**`query`=`tp_id_or_ref`**）→ **`case10_target_tp_pdf_search.json`**；**底图**：若 **`page_pdf==1`** 且有 **`assembly_drawing_page1_png`** → **复制** → **`case10_assembly_drawing.png`**，**否则** **`pdf_page_to_image`**（**dpi=864**）→ **`case10_assembly_drawing.png`**；**`run_python`**（Step0C）→ **`case10_target_tp_work_roi.png`**（必写）+ **`case10_assembly_drawing_tp_marked.png`**（+ 可选 **`case10_target_tp_opencv_debug.png`**）。

### Part B — 位号最大 IC（底图 **`tp_marked`**）
1. **StepB1**：`**view_image`** + **`case10_assembly_spatial_description.md`** + **`case10_assembly_vlm_hints.json`**。
2. **StepB2**：**`run_python`**（读 **`tp_marked`**）。
3. **StepB3**：**最终 bbox 定稿后** **`annotate_image` 覆盖 **`case10_assembly_largest_ic_box.png`** → **`view_image`（QC）** → **`save_text_file` → `case10_assembly_largest_ic.json`**（`bbox` 与 PNG 一致，`part_b_stepb3_qc` 含 **`final_annotate_overwrote_png_immediately_before_json: true`**）。**`QC_REVISE`** 改框后**必须**再 **`annotate_image` 覆盖**。禁止跳过 `view_image`、禁止只改 JSON 不重画 PNG。

### Part A — 实物最大 IC（**不变**；在 Part B 之后）
1. Step0：**`case10_board_landscape.png`**。
2. Step1–3：hints + OpenCV + **`case10_largest_ic_box.png`** + **`case10_largest_ic.json`**。

### Part C — step02 + `board_tp_marked`
1. **`step02_board_front_anchor.png`**、**`step02_locator_front_anchor.png`**；**`board_tp_marked.png`**。

### Part D — Step3–8（**路径 A** 双 ROI 直接对照 → **路径 B** 规律 + OpenCV）
1. **`step03_mapping.json`**（**`mapping_method`=`case10_dual_roi_layout`**）、**`step03_prior_on_board.png`**
2. **`step04_roi_crop.png`** + **`step04_locator_roi_crop.png`**（**同尺寸**）→ **StepD4.0** **`case10_dual_roi_locator_refs.json`** + **`step04_locator_roi_refs.png`**
3. **路径 A（先试）**：**`case10_tp_dual_roi_direct_vlm.json`**（必写）、**`step04_dual_roi_approx_only.png`**（StepD4.5B B1）、**`case10_tp_dual_roi_direct_refined.json`** + **`step04_dual_roi_direct_snap.png`**（B4）；**StepD4.5C（可选）**：`case10_tp_dual_roi_direct_candidates.json`、`step04_dual_roi_direct_candidates.png`、`case10_tp_dual_roi_vlm_pick.json`
4. **路径 B（ fallback 或未采纳 A）**：**`case10_tp_roi_layout_vlm.json`**、**`case10_tp_roi_layout_hints.json`**
5. **`step05_candidates.json`**、**`step57_candidates_scored.png`**
6. **`step08_final_tp.png`**、**`step08_result.json`**、**`finish`**

### 最终产物清单
- **原理图/目标 TP**：**`case10_signal_to_tp.json`**（**`tp_id_or_ref`** + 依据）
- **PDF/TP**：`case10_target_tp_pdf_search.json`、`case10_target_tp_pdf_pick.md`（可选）、**`case10_assembly_drawing.png`**（默认：`pdf_page_to_image`、**dpi=864**；**或** **`page_pdf==1`** 且提供 **`assembly_drawing_page1_png` 时由其复制**）、**`case10_target_tp_work_roi.png`**（必）、`case10_assembly_drawing_tp_marked.png`、`case10_target_tp_opencv_debug.png`（可选）
- **位号 IC**：`case10_assembly_spatial_description.md`、**`case10_assembly_vlm_hints.json`**、`case10_assembly_largest_ic_box.png`、**`case10_assembly_largest_ic.json`**、`case10_assembly_opencv_debug.png`（可选）
- **实物 IC**：`case10_board_landscape.png`、`case10_spatial_description.md`、**`case10_vlm_hints.json`**、`case10_largest_ic_box.png`、`case10_largest_ic.json`、`case10_opencv_debug.png`（可选）
- **Step2 锚点 + 映射**：`step02_board_front_anchor.png`、**`step02_locator_front_anchor.png`**、**`board_tp_marked.png`**、**`case10_tp_board_mapping.json`**（可选复查）
- **Part D（Step3–8）**：`step03_mapping.json`、`step03_prior_on_board.png`、`step04_roi_crop.png`、**`step04_locator_roi_crop.png`**、**`case10_dual_roi_locator_refs.json`**、**`step04_locator_roi_refs.png`**、**`case10_tp_dual_roi_direct_vlm.json`**、**`step04_dual_roi_approx_only.png`**、**`case10_tp_dual_roi_direct_refined.json`**、**`step04_dual_roi_direct_snap.png`**（路径 A 主流程）；**可选 fallback**：`case10_tp_dual_roi_direct_candidates.json`、`step04_dual_roi_direct_candidates.png`、`case10_tp_dual_roi_vlm_pick.json`；**`case10_tp_roi_layout_vlm.json`** / **`case10_tp_roi_layout_hints.json`**（路径 B）、`step05_candidates.json`、`step57_candidates_scored.png`、`step08_final_tp.png`、`step08_result.json`
- **运行时 `finish` 门禁**（见 `agent/_validate_skill_contract`）：**`debug/`** 下列关键 PNG/JSON 须存在；**`finish.answer.pixel`** = 实物整板 **[x,y]**，与 **`step08_result.json`** / **`step08_final_tp.png`** 一致（±1px）；**不**强制 `progress/step_*.md`。
- **Part D 结束须** **`finish`**（**禁止**默认 **`run_candidate_pipeline`**；**`mapping_method`** 须为 **`case10_dual_roi_layout`**）。

