# API Contract

## GET `/`

返回内置前端页面。页面用于提交一次 debugging bench case，当前输入字段收敛为：

- `Instruction`
- `Case ID`
- `Operator`
- `Camera_image`
- `Bit_image`
- `Schematic_Diagram`

## POST `/api/runs`

创建并同步执行一次 bench debugging run。当前版本停用 RAG；`Camera_image` 给 VLM 使用，`Bit_image` 和 `Schematic_Diagram` 会被整理成 YAML 后交给 mock 大模型模块。mock 大模型会返回 MG400 的 `x y z r`。

### Request

```json
{
  "Instruction": "我要确认输入电压 VCP 是否正常",
  "Case_ID": "case-001",
  "Operator": "demo-user",
  "Camera_image": {
    "name": "PCBA_IMG.jpg",
    "type": "image/jpeg",
    "size": 2048
  },
  "Bit_image": [
    {
      "name": "位号图7.29_01(12).png",
      "type": "image/png",
      "size": 1024
    }
  ],
  "Schematic_Diagram": [
    {
      "name": "voyah_hvac_v01_20240729_01.png",
      "type": "image/png",
      "size": 1024
    }
  ]
}
```

为了兼容现有 mock pipeline，后端会把这些字段映射为内部结构：

- `Instruction` -> `input.command` / `input.instruction`
- `Case_ID` -> `input.caseId`
- `Operator` -> `input.operator`
- `Camera_image` -> `input.cameraImage` / `input.visualCapture`
- `Bit_image` + `Schematic_Diagram` -> `input.modelAttachments`

### Response

```json
{
  "runId": "run_...",
  "state": "REPORTING",
  "input": {
    "instruction": "我要确认输入电压 VCP 是否正常",
    "caseId": "case-001",
    "operator": "demo-user",
    "cameraImage": {},
    "bitImages": [],
    "schematicDiagrams": []
  },
  "vlmObservation": {
    "modelInputSummary": {
      "attachmentCount": 2,
      "bitImageCount": 1,
      "schematicCount": 1
    }
  },
  "modelInputYaml": "Instruction: ...",
  "modelInputYamlFile": "C:\\Users\\FX506L\\OneDrive\\文档\\New project 2\\output\\yaml\\run_....yaml",
  "modelOutput": {
    "model": "mock-large-model-v0",
    "inputFormat": "yaml",
    "testPoint": "TP_VCP",
    "confidence": 0.88,
    "mg400Pose": {
      "x": 245.6,
      "y": -32.4,
      "z": 78.2,
      "r": 91.5
    }
  },
  "ragEvidence": [],
  "plan": {
    "steps": [
      {
        "kind": "ARM_MOTION",
        "command": "MOVE_TO_MG400_POSE",
        "targetPose": {
          "x": 245.6,
          "y": -32.4,
          "z": 78.2,
          "r": 91.5
        }
      }
    ]
  },
  "execution": {
    "arm": [
      {
        "tcpCommand": "MovJ(245.6,-32.4,78.2,91.5)"
      }
    ]
  },
  "report": {},
  "timeline": []
}
```

## GET `/api/runs/:runId`

读取内存中的 run 结果。服务重启后历史数据会丢失。

## GET `/health`

服务健康检查。
