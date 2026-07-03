# VLM Agent 远程服务器部署指南

## 架构概览

```
┌─────────────────────────────┐       ┌──────────────────────────────┐
│  你的本地机器                 │       │  服务器 (AI/模型团队维护)     │
│                             │       │                              │
│  Inputdemo/ (Node.js)       │ HTTP  │  Debugging-agent-v2/ (Python)│
│  ├─ Web UI                  │ ────→ │  ├─ agent/service.py         │
│  ├─ 编排引擎                 │ 上传   │  ├─ 工具链 & 提示词          │
│  ├─ MG400 控制              │ case  │  ├─ .env (模型配置)          │
│  └─ RemoteVlmAgentRunner ───│ 文件  │  └─ FastAPI :8000           │
│                             │ ←──── │                              │
│  ✅ 你可以任意修改这部分      │ 返回  │  🔒 AI团队独立维护            │
│     不会影响 VLM Agent       │ 结果  │     你不会改动到              │
└─────────────────────────────┘       └──────────────────────────────┘
```

**关键点**：
- VLM Agent（视觉大模型推理）代码完全在服务器上运行
- 你的 Node.js 编排层通过 HTTP 上传图片/PDF/YAML，服务器执行推理后返回坐标
- 两边的代码互不干扰

---

## 一、服务器部署（AI/模型团队执行）

### 1.1 环境要求

- Python 3.10+
- 网络可访问的 VLM 模型端点（已配置在 `.env` 中）

### 1.2 上传代码到服务器

将整个 `Vlm agent/Debugging-agent-v2/` 目录上传到服务器：
```bash
# 例如放到 /opt/vlm-agent/
scp -r "Vlm agent/Debugging-agent-v2/" user@server:/opt/vlm-agent/
```

### 1.3 安装依赖

```bash
cd /opt/vlm-agent/Debugging-agent-v2

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate    # Windows

# 安装依赖（新增了 FastAPI/uvicorn）
pip install -r requirements.txt
```

### 1.4 配置模型凭证

编辑 `.env` 文件，填入你的 VLM 模型信息：
```env
VLM_BASE_URL=https://your-model-endpoint.com/v1
VLM_API_KEY=your_api_key_here
VLM_MODEL=your_vision_model

# 常规参数
VLM_TEMPERATURE=0.2
VLM_MAX_TOKENS=4096
VLM_USE_NATIVE_TOOLS=true

# thinking 模式（如使用 DeepSeek/Intern-S1）
VLM_ENABLE_THINKING=false
```

### 1.5 启动服务

**方式 A：直接启动（测试用）**
```bash
# Linux/macOS
chmod +x run_server.sh
./run_server.sh

# Windows
run_server.bat

# 或手动指定端口
./run_server.sh 9000
```

**方式 B：systemd 服务（生产环境推荐）**
```bash
# 1. 编辑部署文件中的路径
sudo cp deploy/vlm-agent.service /etc/systemd/system/
sudo vi /etc/systemd/system/vlm-agent.service  # 确认路径

# 2. 创建日志目录
sudo mkdir -p /var/log/vlm-agent

# 3. 启动
sudo systemctl daemon-reload
sudo systemctl enable vlm-agent
sudo systemctl start vlm-agent

# 4. 检查状态
sudo systemctl status vlm-agent
curl http://localhost:8000/health
```

### 1.6 验证服务

```bash
# 健康检查
curl http://<服务器IP>:8000/health
# → {"ok":true,"service":"debugging-agent-v2"}

# 测试一个 case（从服务器本地文件）
curl -X POST http://<服务器IP>:8000/v1/runs \
  -H "Content-Type: application/json" \
  -d '{"task_file":"data/cases/case_001/task.yaml","max_steps":25}'
```

---

## 二、本地配置（你执行）

### 2.1 设置环境变量

在你的 Node.js 项目根目录，创建或编辑 `.env` 文件：

```env
# 指向远程 VLM Agent 服务器
VLM_AGENT_SERVICE_URL=http://<服务器IP>:8000

# 可选配置
VLM_AGENT_MAX_STEPS=80
VLM_AGENT_TIMEOUT_MS=3600000
```

### 2.2 启动你的服务

```bash
npm start
```

现在 Node.js 的 `factory.js` 会**自动检测** `VLM_AGENT_SERVICE_URL` 指向非本地地址，使用 `RemoteVlmAgentServiceRunner` 将 case 文件上传到远程服务器执行。

### 2.3 手动控制 Runner 模式

| 环境变量 | 行为 |
|---|---|
| 不设置（默认） | 自动检测 URL 是否远程 |
| `VLM_AGENT_RUNNER=cli` | 本地 CLI 启动（需要本地 VLM Agent 目录） |
| `VLM_AGENT_RUNNER=local-svc` | 本地 FastAPI 服务 + CLI 回退 |
| `VLM_AGENT_RUNNER=remote-svc` | 强制远程服务模式 |

---

## 三、完整工作流

```
1. 操作员在 Web UI 上传 PCB 图片/位号图/原理图
         │
2. Node.js Inputdemo 接收 → VlmAgentCaseAdapter 创建 case 目录
         │
3. RemoteVlmAgentRunner 将整个 case 打包上传到服务器
   POST /v1/runs/upload (multipart: task.yaml + all images/PDFs)
         │
4. 服务器 VLM Agent 执行推理（多轮工具调用）
   - 搜索 PDF → 渲染位号图
   - VLM + OpenCV 定位测试点
   - 对齐位号图与实物板
   - 输出像素坐标 (x, y)
         │
5. Node.js 轮询等待完成 → 获取 final_answer
         │
6. 将像素坐标转换为 MG400 运动命令 → Robot Gateway → 机械臂执行
```

---

## 四、AI/模型团队如何更新 VLM Agent

由于 VLM Agent 代码在服务器上，AI 团队可以直接更新：

```bash
# 1. SSH 到服务器
ssh user@server

# 2. 拉取最新代码
cd /opt/vlm-agent/Debugging-agent-v2
git pull

# 3. 更新依赖（如有变化）
source .venv/bin/activate
pip install -r requirements.txt

# 4. 重启服务
sudo systemctl restart vlm-agent
```

**你不需要做任何事**——你的 Node.js 服务会自动使用更新后的 VLM Agent。

---

## 五、API 参考

| 端点 | 方法 | 说明 |
|---|---|---|
| `/health` | GET | 健康检查 |
| `/v1/runs` | POST | 从服务器本地路径创建 run |
| `/v1/runs/upload` | POST | 上传 case 文件 + 创建 run（multipart） |
| `/v1/runs/{id}` | GET | 获取 run 状态和结果 |
| `/v1/runs/{id}/events` | GET | 获取 SSE 事件流 |
| `/v1/runs/{id}/cancel` | POST | 取消 run |
| `/v1/runs/{id}/observations` | POST | 发送外部观察 |
| `/v1/runs/{id}/files/{path}` | GET | 下载生成的 workspace 文件 |

---

## 六、端口汇总

| 服务 | 默认端口 | 环境变量 |
|---|---|---|
| Node.js Web/API | 3000 | `PORT` |
| Robot Gateway | 8010 | `ROBOT_GATEWAY_PORT` |
| **VLM Agent Service** | **8000** | `VLM_AGENT_SERVICE_PORT` |

---

## 七、故障排查

### 服务器端
```bash
# 检查服务是否运行
curl http://<服务器IP>:8000/health

# 查看日志
sudo journalctl -u vlm-agent -f

# 检查端口是否开放
sudo ss -tlnp | grep 8000
```

### 本地
```bash
# 检查连通性
curl http://<服务器IP>:8000/health

# 强制使用远程模式
set VLM_AGENT_RUNNER=remote-svc
npm start
```
