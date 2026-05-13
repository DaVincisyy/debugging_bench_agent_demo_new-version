# API Contract

## GET `/`

返回内置前端页面。页面用于提交一次 debugging bench case，包含：

- 文字或语音 prompt
- 给 VLM 的实物图 / bench 图像
- 给大模型的原理图、位号图、位图、PDF 等附件

## POST `/api/runs`

创建并同步执行一次 bench debugging run。当前版本停用 RAG，`ragEvidence` 返回空数组；图纸和位图通过 `modelAttachments` 进入模型输入。

### Request

```json
{
  "prompt": {
    "mode": "text",
    "text": "我要确认输入电压 VCP 是否正常"
  },
  "visualCapture": {
    "imageRef": "PCBA_IMG.jpg",
    "target": "vlm"
  },
  "modelAttachments": [
    {
      "kind": "schematic",
      "name": "voyah_hvac_v01_20240729_01.png",
      "type": "image/png",
      "size": 1024
    },
    {
      "kind": "layout",
      "name": "位号图7.29_01(12).png",
      "type": "image/png",
      "size": 1024
    }
  ],
  "context": {
    "caseId": "case-001",
    "operator": "demo-user"
  }
}
```

为了兼容旧调用，`command` 仍然可以直接传入。新前端会优先使用 `prompt.text` 作为任务目标。

### Response

```json
{
  "runId": "run_...",
  "state": "REPORTING",
  "input": {
    "command": "我要确认输入电压 VCP 是否正常",
    "prompt": {},
    "visualCapture": {},
    "modelAttachments": []
  },
  "plan": {
    "objective": "我要确认输入电压 VCP 是否正常",
    "steps": []
  },
  "vlmObservation": {
    "modelInputSummary": {
      "attachmentCount": 2,
      "schematicCount": 1,
      "layoutCount": 1
    }
  },
  "ragEvidence": [],
  "execution": {
    "arm": [],
    "equipment": []
  },
  "report": {
    "summary": "...",
    "findings": [],
    "measurements": [],
    "nextActions": []
  },
  "timeline": []
}
```

## GET `/api/runs/:runId`

读取内存中的 run 结果。服务重启后历史数据会丢失。

## GET `/health`

服务健康检查。
