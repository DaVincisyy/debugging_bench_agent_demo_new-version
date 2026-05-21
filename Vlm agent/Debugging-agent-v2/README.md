# VLM Agent — PCBA 测试点像素定位框架

> 这是整个 **非标机械臂 PCBA 测试平台** 中第 6 个模块 **`VLM visual-decision core`** 的
> 独立开发 / 调试骨架。它把“一张位号图上的 TP 标记”翻译成“camera 实拍照片中的像素点”，
> 输出后续由模块 7（机械臂控制）直接消费。

---

## 1. 为什么需要这样一个 agent

在当前的工作流里，你只能把文件丢进某个 VLM 的对话框里一问一答：

- VLM 没办法自己跑代码（做 SIFT / template match / 坐标换算），
- 没办法迭代 “先裁一个小图 → 再放大看一下” 这样的闭环，
- 没办法记录 / 复现每一次推理过程，
- 更没办法在你想换一个 VLM 时保留之前的 prompt、工具、评测脚本。

于是你变成了模型的双手，来回粘贴、截图、计算。本框架把这些琐事交给 agent：

| 能力 | 框架里是什么 |
| --- | --- |
| 工具调用 / function calling | `agent/tools.py` + OpenAI 原生 `tools=` 协议 |
| 代码运行（PIL / OpenCV / numpy） | `run_python` 工具（进程内执行，状态跨 step 保留） |
| Shell 命令 | `run_shell` |
| 读原理图、BOM、CSV | `read_text_file`（支持行区间，避免撑爆上下文） |
| 看图 / 裁图 / 画标记验证 | `view_image` / `crop_image` / `annotate_image` |
| 多轮思考 + planning | `agent/agent.py` 的 plan → tool → observe 主循环 |
| 换 VLM | 改 `.env` 里的三行（`VLM_BASE_URL` / `VLM_API_KEY` / `VLM_MODEL`）即可 |
| 离线复现 / 训练 | 每次运行落盘 `workspace/runs/<timestamp>/summary.json` + `messages.final.jsonl` |

---

## 2. 项目结构

```
test_platform/
├─ agent/
│  ├─ __init__.py          # 对外导出 Agent / Config / Tool* 等
│  ├─ __main__.py          # 让 `python -m agent ...` 能跑
│  ├─ cli.py               # 命令行解析
│  ├─ config.py            # 读 .env + YAML overrides
│  ├─ llm_client.py        # OpenAI 兼容客户端（vision + tool calling + fallback）
│  ├─ tools.py             # Tool / ToolRegistry 基类
│  ├─ builtin_tools.py     # 内置工具集合
│  ├─ prompts.py           # TP 定位任务的 system prompt
│  ├─ agent.py             # 主循环
│  └─ utils.py             # 小工具（图片转 data URL、截断等）
├─ data/
│  └─ cases/
│     └─ case_001_tp12_front/
│        ├─ task.yaml       # 任务说明（question + inputs）
│        ├─ locator.png     # 位号图（标记 TP 的版本）
│        ├─ schematic.txt   # 原理图 netlist
│        ├─ front.jpg       # 正面实拍
│        └─ back.jpg        # 反面实拍（可选）
├─ tests/
│  ├─ test_unit.py          # 单元测试（配置 / schema / 沙箱等）
│  └─ test_agent_e2e.py     # 端到端 mock 测试
├─ workspace/               # 运行时产物（被 .gitignore）
│  └─ runs/<时间戳>/         # 每次 run 的 summary.json / messages.final.jsonl
├─ .env                     # 本地凭据（被 .gitignore）
├─ .env.example
├─ requirements.txt
└─ README.md
```

---

## 3. 安装

```bash
python -m venv .venv
.venv\Scripts\activate           # Windows PowerShell
# source .venv/bin/activate      # Linux / macOS

pip install -r requirements.txt
```

依赖：`openai >= 1.40`、`python-dotenv`、`pyyaml`、`Pillow`、`opencv-python`、`numpy`、`rich`。

---

## 4. 配置 VLM（换模型只改 `.env`）

仓库里已带一份预填好的 `.env`（指向 `deepseek-v4-pro`），你**只需要填上自己的
DeepSeek API Key**：

```env
VLM_BASE_URL=https://api.deepseek.com      # 注意：不带 /v1
VLM_API_KEY=                                # ← 填你的 key
VLM_MODEL=deepseek-v4-pro
VLM_USE_NATIVE_TOOLS=true
VLM_ENABLE_THINKING=true                   # DeepSeek V4 思考模式
VLM_REASONING_EFFORT=high
```

> Key 在 <https://platform.deepseek.com/api_keys> 申请。  
> `https://platform.deepseek.com/...` 是控制台网页，**不是 API endpoint**，
> 不要当作 `VLM_BASE_URL` 使用。API endpoint 是 `https://api.deepseek.com`。

想换别家 VLM，只要改 `.env` 三行（或用 `--model` / `--base-url` 临时覆盖）：

| 提供商 | `VLM_BASE_URL` | 示例 `VLM_MODEL` | 备注 |
| --- | --- | --- | --- |
| DeepSeek | `https://api.deepseek.com` | `deepseek-v4-pro`, `deepseek-v4-flash` | **不带 `/v1`**；支持思考模式 |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o`, `gpt-4.1`, `o4-mini` | o-系列可用 `VLM_REASONING_EFFORT` |
| 阿里百炼 DashScope | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-vl-max` | |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4v-plus` | |
| 火山豆包 | `https://ark.cn-beijing.volces.com/api/v3` | `doubao-vision-pro-32k` | |
| 本地 vLLM / LM Studio | `http://localhost:8000/v1` | 任意已加载的多模态模型 | 离线 |

换到不支持思考模式的家时，把 `VLM_ENABLE_THINKING=false`、`VLM_REASONING_EFFORT=` 留空即可；
这两个字段只有真值时才会发送出去，不会污染其他提供商的请求。

> 如果所选 VLM 不支持原生 `tools=` 协议，把 `VLM_USE_NATIVE_TOOLS=false`，
> 框架会改用 `<tool_call>{...}</tool_call>` JSON 标签协议，由我们自己解析。

临时覆盖（不改 `.env`）：

```bash
python -m agent run data/cases/case_001_tp12_front/task.yaml \
    --model qwen-vl-max \
    --base-url https://dashscope.aliyuncs.com/compatible-mode/v1
```

### 4.1 DeepSeek V4 思考模式细节

- `VLM_ENABLE_THINKING=true` 会在请求里加 `extra_body={"thinking": {"type": "enabled"}}`。
- `VLM_REASONING_EFFORT=high`（或 `max`）传给 `reasoning_effort` 字段。
- 模型返回的思维链（`reasoning_content`）会被框架自动保存，并在**带有工具调用**的后续轮次
  回传给 API——这是 DeepSeek 官方要求（否则会 400）。整条代码路径对用户透明。

---

## 5. 准备输入数据（多 case 组织）

每组测试 = `data/cases/` 下的一个子目录。`task.yaml` 和它要用的素材放同一层：

```
data/
└─ cases/
   ├─ case_001_tp12_front/
   │   ├─ task.yaml       ← 任务说明
   │   ├─ locator.png     ← 位号图（标记了 TP 的版本）
   │   ├─ schematic.txt   ← 原理图 netlist
   │   ├─ front.jpg       ← 正面实拍
   │   └─ back.jpg        ← 反面实拍（可选）
   ├─ case_002_tp07_back/
   │   └─ ...
   └─ ...
```

示例 `data/cases/case_001_tp12_front/task.yaml`（仓库里已有）：

```yaml
question: |
  请在 front_photo 中精确给出 TP12 对应的像素坐标...
inputs:
  locator_image: locator.png      # ← 相对 task.yaml 所在目录
  schematic_text: schematic.txt
  front_photo: front.jpg
  back_photo: back.jpg
  board_id: "XYZ123-REV-B"
  engineer_note: "板子正面朝上，摄像头距离约 25cm..."
```

规则：

- `inputs` 下的**相对路径** = **相对 `task.yaml` 所在目录**解析。整个 case 目录
  拷到别处也不会坏；绝对路径保持原样。
- 后缀是图片 (`.png/.jpg/.jpeg/.bmp/.webp/.tiff`) 的路径会**自动以图像形式**附到首轮 user turn。
- 其他存在的路径会作为 `[file]` 上下文告诉 agent，它自己决定何时 `read_text_file`。
- 普通字符串（`board_id`、`engineer_note` 等）直接拼在提示里。

> 要开新 case：复制 `data/cases/case_001_tp12_front/` 整个目录，改个 ID / 描述即可。

---

## 6. 运行

两种模式：

### 6.1 基于 case 的 YAML 任务

```bash
python -m agent run data/cases/case_001_tp12_front/task.yaml
```

可选参数：

```bash
python -m agent run data/cases/case_001_tp12_front/task.yaml \
    --model deepseek-v4-pro \
    --max-steps 30 \
    --name tp12-trial-1
```

批量跑所有 case：

```powershell
# Windows PowerShell
foreach ($t in Get-ChildItem data/cases -Recurse -Filter task.yaml) {
    python -m agent run $t.FullName --name batch
}
```

```bash
# Linux / macOS
for t in data/cases/*/task.yaml; do
    python -m agent run "$t" --name batch
done
```

### 6.2 临时问一个问题

```bash
python -m agent ask "TP12 在正面照片的像素位置？" \
    --image locator=data/cases/case_001_tp12_front/locator.png \
    --image front=data/cases/case_001_tp12_front/front.jpg \
    --image back=data/cases/case_001_tp12_front/back.jpg \
    --file   schematic=data/cases/case_001_tp12_front/schematic.txt \
    --note   board_id=XYZ123-REV-B
```

运行时你会看到：

- `cyan` 面板 = 每一轮 assistant 的思考 + 调用的工具；
- `green` / `red` 面板 = 每次工具执行的参数与结果；
- 最后一个面板 = 最终答案和停止原因。

---

## 7. Agent 的标准输出

`finish` 工具提交的 payload（会写进 `workspace/runs/<stamp>/summary.json`）：

```json
{
  "tp_id": "TP12",
  "camera_view": "front",
  "pixel": [1287, 642],
  "confidence": 0.88,
  "needs_user_help": false,
  "user_message": null,
  "reasoning": "从 locator 标记对应 R32 附近，在 front_photo 上通过 ORB 配准定位…"
}
```

当 VLM 发现测试点在当前镜头不可见的那一面时：

```json
{
  "tp_id": "TP12",
  "camera_view": "back",
  "pixel": null,
  "confidence": 0.95,
  "needs_user_help": true,
  "user_message": "TP12 位于板子背面，请工程师将板子翻面后重新拍摄。",
  "reasoning": "locator 标记位于背面 silk-screen 上，正面照片中对应区域无焊盘…"
}
```

这个字段直接对应模块 7（机械臂）和模块 8（语音提示）的入口。

---

## 8. 内置工具一览

| 工具名 | 作用 |
| --- | --- |
| `list_files(path)` | 列目录 / 看单文件元数据 |
| `read_text_file(path, start_line?, end_line?)` | 读原理图、netlist、BOM |
| `save_text_file(path, content)` | 在 workspace 内保存文本 |
| `image_info(path)` | 取宽高 / 模式 |
| `view_image(path, note?)` | 把图片附到下一轮，让 VLM 真正“看见” |
| `crop_image(path, bbox, out_path)` | 裁图并自动 view |
| `annotate_image(path, points, out_path, radius?)` | 在图上画十字标记 + 文本标签，自动 view |
| `run_python(code, timeout?)` | 进程内执行 Python；`np`/`cv2`/`Image` 已导入；**跨 step 保留变量** |
| `run_shell(command, timeout?)` | 在 workspace 下执行 shell |
| `finish(answer)` | 提交结构化最终答案并停止 |

### 扩展自己的工具

```python
from agent import Agent, load_config, Tool, build_default_registry, ToolResult

cfg = load_config()
registry = build_default_registry(cfg.workspace_dir)

def my_measure(path: str, threshold: float = 0.5) -> ToolResult:
    # …做点事…
    return ToolResult(text="done", images=["workspace/out.png"])

registry.register(Tool(
    name="my_measure",
    description="Measure something on the board photo.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "threshold": {"type": "number", "default": 0.5},
        },
        "required": ["path"],
    },
    fn=my_measure,
))

Agent(cfg, registry=registry).run(question="…", inputs={...})
```

---

## 9. 它如何帮你提升效率

1. **一键换 VLM**：改 `.env` 的 `VLM_BASE_URL` / `VLM_API_KEY` / `VLM_MODEL` 就能用同一套
   prompt、同一套工具、同一份任务 YAML 对比 GPT-4o、Qwen-VL、GLM-4V、DeepSeek-VL 等，
   不用每家去改代码或 SDK。
2. **免去人肉当双手**：你以前要手动裁图、算坐标、把数值回填给 VLM。现在它用
   `crop_image` / `run_python` / `annotate_image` 自己闭环；你只看结果。
3. **可复现的调试记录**：`workspace/runs/<stamp>/summary.json` 保存了完整的
   思考链、工具参数、工具返回、最终答案，排查和做 regression 集非常容易。
4. **批量跑回归**：把多个样例塞进 `data/cases/*/task.yaml`，写个循环就能自动跑：

    ```bash
    for t in data/cases/*/task.yaml; do python -m agent run "$t" --name batch; done
    ```

   对比不同模型的 `summary.json` 就能做 offline 评测。
5. **fallback 机制**：如果你要测的 VLM 不支持原生 function calling，`VLM_USE_NATIVE_TOOLS=false`
   会自动切到 JSON 标签协议。同一段业务代码 / prompt 不需要重写。
6. **安全围栏**：所有写操作被限制在 `workspace/` 下，`run_shell` 也 `cwd=workspace`；
   读文件允许工作区外，方便直接引用 `data/` 下的原始资料。
7. **为后续模块做胶水**：`finish` 返回的结构化 payload（`camera_view` / `pixel` /
   `needs_user_help` / `user_message`）就是模块 7（机械臂）和模块 8（语音/消息）的
   标准入参，后面接管道非常直接。

---

## 10. 后续接入

- **模块 1/2 microphone + terminal input**：可复用 `ask` 子命令 + 语音转文本；
  文字直接作为 `question`，参考文件用 `--file` 传。
- **模块 3 文件检索**：未来可以加一个 `search_file` 工具，把扫板号 → 检索 CAD 资料的
  逻辑封装成工具；对 agent 来说只是又多一个可调用函数。
- **模块 5 real-time camera**：把“抓一张实时照片”也做成工具，例如
  `capture_camera(side="front") -> image`，让 VLM 自主决定何时拍新一张。
- **模块 7 robotic arm**：在 `finish` 之后把 JSON 喂给机械臂控制程序即可；
  当前 CLI 会以 exit-code 0 表示拿到答案、2 表示没拿到。
- **模块 8 user assistance**：`needs_user_help + user_message` 字段可直接驱动 TTS。

---

## 11. 自检 / 测试

仓库自带两份零 API 开销的测试：

```bash
python tests\test_unit.py    # env 校验、schema、fallback 协议、写沙箱
python tests\test_agent_e2e.py   # mock VLM 的完整 plan→tool→observe 流程
```

`test_agent_e2e.py` 会自动造一张 400×300 的位号图（红点）、一张 1600×1200 的假实拍图
（放大的红点）和一份 netlist，然后 **脚本化** 一串 VLM 回复，让 Agent 依次执行
`list_files → read_text_file → view_image → crop_image → run_python(HSV 定位) →
annotate_image → finish`，最终断言：

- 停止原因 = `finish-tool-called`
- 最终像素距离真值 (800, 600) 在 ±2px 内
- `workspace/runs/<stamp>/summary.json` 与 `messages.{init,final}.jsonl` 都被写入
- 图像确实作为 `image_url` data URL 在下一轮 user turn 中回传给“模型”

你可以把这个测试当成 **迁移到新 VLM 时的 checklist**：只要新模型跑得出同样的形状，
基础框架层就肯定没问题，剩下就是 prompt / 工具逻辑层的事。

---

## 12. 常见问题

- **提示 `Missing required env vars`**：没复制 `.env.example` 成 `.env` 或没填 key。
- **`401` / `AuthenticationError`**：base_url 与 model 不匹配（例如 DeepSeek key 配了 OpenAI 的 url）。
- **图像被拒绝 / 尺寸太大**：多数 VLM 对单图尺寸和 base64 大小有上限，可用 `crop_image`
  把局部小图送进去；或事先在 `data/` 里把原图下采样。
- **模型反复 plan 不调用工具**：把 `VLM_USE_NATIVE_TOOLS` 关掉切到 JSON 标签协议，或
  换一个 prompt-following 更好的模型试试。
- **Windows 下 `ImageFont.truetype("arial.ttf")` 失败**：已经有 fallback 到默认字体，功能
  不受影响，只是字形略丑。
