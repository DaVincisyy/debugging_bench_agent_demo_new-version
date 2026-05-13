# Debugging Bench Agent

最小可运行版本，用于先验证接口和流程：

```text
Prompt + VLM Image + Model Attachments
  -> LLM Input Core
  -> VLM Bench Awareness
  -> Agent Plan
  -> Arm/Equipment Mock Execution
  -> GUI/Report Result
```

当前版本的 VLM、机械臂、测试设备都使用 mock adapter。RAG 暂时停用，原理图、原理图位图、位号图、位号图位图等文件会通过 `modelAttachments` 作为大模型输入附件传入。

## 项目结构

```text
src/
  adapters/       外部能力接口实现，当前为 mock
  agent/          Agent 编排与状态流转
  api/            HTTP API 服务与内置前端页面
  cli/            本地端到端演示入口
  domain/         数据结构、状态、错误类型
  utils/          通用工具
docs/
  api.md          API 契约说明
test/
  pipeline.test.js
```

## 运行

```bash
npm run demo
```

启动 HTTP 服务和前端页面：

```bash
npm start
```

默认监听 `http://localhost:3000`。打开根路径 `/` 可以使用前端页面提交：

1. 文字或语音 prompt
2. 给 VLM 的实物图 / bench 图像
3. 给大模型的原理图、位号图、位图、PDF 等附件

## 示例请求

```bash
curl -X POST http://localhost:3000/api/runs ^
  -H "Content-Type: application/json" ^
  -d "{\"prompt\":{\"mode\":\"text\",\"text\":\"我要确认输入电压 VCP 是否正常\"},\"visualCapture\":{\"imageRef\":\"PCBA_IMG.jpg\"},\"modelAttachments\":[{\"kind\":\"schematic\",\"name\":\"voyah_hvac_v01_20240729_01.png\",\"type\":\"image/png\"},{\"kind\":\"layout\",\"name\":\"位号图7.29_01(12).png\",\"type\":\"image/png\"}]}"
```

## 测试

```bash
npm test
```
