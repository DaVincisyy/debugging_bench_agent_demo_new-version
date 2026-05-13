# Debugging Bench Agent Interface I/O

本文档说明当前最小可运行版本中，每一处接口的期望输入和输出 JSON。

## 1. HTTP API

### 1.1 Create Run

`POST /api/runs`

#### Input

```json
{
  "command": "Inspect CAN transceiver board and measure 5V rail ripple",
  "visualCapture": {
    "imageRef": "Version 1.png",
    "cameraId": "bench-overview"
  },
  "context": {
    "operator": "demo-user",
    "benchId": "kpit-bench-01"
  }
}
```

#### Output

```json
{
  "runId": "run_mp0teox4_8w7vllfe",
  "state": "REPORTING",
  "input": {
    "command": "Inspect CAN transceiver board and measure 5V rail ripple",
    "visualCapture": {
      "imageRef": "Version 1.png",
      "cameraId": "bench-overview"
    },
    "context": {
      "operator": "demo-user",
      "benchId": "kpit-bench-01"
    }
  },
  "plan": {
    "objective": "Inspect CAN transceiver board and measure 5V rail ripple",
    "evidenceIds": ["manual-5v-ripple", "gerber-j3", "procedure-can-u7"],
    "steps": []
  },
  "vlmObservation": {},
  "ragEvidence": [],
  "execution": {
    "arm": [],
    "equipment": []
  },
  "report": {},
  "timeline": []
}
```

### 1.2 Get Run

`GET /api/runs/:runId`

#### Input

```json
{
  "runId": "run_mp0teox4_8w7vllfe"
}
```

`runId` 来自 URL path，不需要 request body。

#### Output

```json
{
  "runId": "run_mp0teox4_8w7vllfe",
  "state": "REPORTING",
  "input": {},
  "plan": {},
  "vlmObservation": {},
  "ragEvidence": [],
  "execution": {},
  "report": {},
  "timeline": []
}
```

### 1.3 Health Check

`GET /health`

#### Input

```json
{}
```

#### Output

```json
{
  "ok": true,
  "service": "debugging-bench-agent"
}
```

## 2. LLM Input Core

代码位置：`src/agent/inputCore.js`

负责校验并标准化用户输入。

#### Input

```json
{
  "command": "Inspect CAN transceiver board and measure 5V rail ripple",
  "visualCapture": {
    "imageRef": "Version 1.png",
    "cameraId": "bench-overview"
  },
  "context": {
    "operator": "demo-user",
    "benchId": "kpit-bench-01"
  }
}
```

#### Output

```json
{
  "command": "Inspect CAN transceiver board and measure 5V rail ripple",
  "visualCapture": {
    "imageRef": "Version 1.png",
    "cameraId": "bench-overview"
  },
  "context": {
    "operator": "demo-user",
    "benchId": "kpit-bench-01"
  }
}
```

#### Error Output

```json
{
  "error": "Field 'command' is required."
}
```

## 3. VLM Adapter Mock

代码位置：`src/adapters/mockVlmClient.js`

后续真实 VLM 模型应实现同样的输入输出结构。

#### Input

```json
{
  "command": "Inspect CAN transceiver board and measure 5V rail ripple",
  "visualCapture": {
    "imageRef": "Version 1.png",
    "cameraId": "bench-overview"
  },
  "context": {
    "operator": "demo-user",
    "benchId": "kpit-bench-01"
  }
}
```

#### Output

```json
{
  "model": "mock-vlm-v0",
  "imageRef": "Version 1.png",
  "confidence": 0.86,
  "benchOverview": {
    "boardDetected": true,
    "instrumentsDetected": ["oscilloscope", "programmable-power-supply"],
    "armReachableZones": ["top-left", "center", "probe-pad-j3"]
  },
  "locations": [
    {
      "id": "probe-pad-j3",
      "label": "J3 5V rail probe pad",
      "normalizedBox": {
        "x": 0.57,
        "y": 0.42,
        "width": 0.08,
        "height": 0.06
      }
    },
    {
      "id": "can-transceiver-u7",
      "label": "CAN transceiver U7",
      "normalizedBox": {
        "x": 0.36,
        "y": 0.48,
        "width": 0.12,
        "height": 0.1
      }
    }
  ],
  "recommendedMeasurements": [
    {
      "signal": "5V_RAIL",
      "locationId": "probe-pad-j3",
      "instrument": "oscilloscope",
      "expectedRange": "4.85V..5.15V",
      "reason": "Command requests bench inspection: Inspect CAN transceiver board and measure 5V rail ripple"
    }
  ]
}
```

## 4. RAG Repository Mock

代码位置：`src/adapters/mockRagRepository.js`

后续可以替换成向量数据库、PLM、Gerber parser 或文档检索服务。

#### Input

```json
{
  "command": "Inspect CAN transceiver board and measure 5V rail ripple",
  "visualCapture": {
    "imageRef": "Version 1.png",
    "cameraId": "bench-overview"
  },
  "context": {
    "operator": "demo-user",
    "benchId": "kpit-bench-01"
  }
}
```

#### Output

```json
[
  {
    "id": "manual-5v-ripple",
    "source": "Bench Hardware Manual",
    "title": "5V rail ripple validation",
    "snippet": "Measure 5V_RAIL at J3 with 20 MHz bandwidth limit. Ripple should remain below 50 mVpp."
  },
  {
    "id": "gerber-j3",
    "source": "Gerber/PLM",
    "title": "J3 probe pad placement",
    "snippet": "J3 exposes the regulated 5V rail near CAN transceiver U7 for debug probing."
  },
  {
    "id": "procedure-can-u7",
    "source": "Test Procedure",
    "title": "CAN transceiver power-on inspection",
    "snippet": "For command 'Inspect CAN transceiver board and measure 5V rail ripple', inspect U7 power pins before running bus activity tests."
  }
]
```

## 5. Agent Planner

代码位置：`src/agent/planner.js`

把 RAG 证据和 VLM 输出合成为可执行计划。

#### Input

```json
{
  "input": {
    "command": "Inspect CAN transceiver board and measure 5V rail ripple",
    "visualCapture": {
      "imageRef": "Version 1.png"
    },
    "context": {}
  },
  "vlmObservation": {
    "recommendedMeasurements": [
      {
        "signal": "5V_RAIL",
        "locationId": "probe-pad-j3",
        "instrument": "oscilloscope",
        "expectedRange": "4.85V..5.15V"
      }
    ]
  },
  "ragEvidence": [
    {
      "id": "manual-5v-ripple"
    },
    {
      "id": "gerber-j3"
    }
  ]
}
```

#### Output

```json
{
  "objective": "Inspect CAN transceiver board and measure 5V rail ripple",
  "evidenceIds": ["manual-5v-ripple", "gerber-j3"],
  "steps": [
    {
      "id": "step-001",
      "kind": "ARM_MOTION",
      "command": "MOVE_PROBE_TO_LOCATION",
      "targetLocationId": "probe-pad-j3"
    },
    {
      "id": "step-002",
      "kind": "EQUIPMENT_MEASUREMENT",
      "instrument": "oscilloscope",
      "signal": "5V_RAIL",
      "locationId": "probe-pad-j3",
      "expectedRange": "4.85V..5.15V"
    },
    {
      "id": "step-003",
      "kind": "VISUAL_CAPTURE",
      "command": "CAPTURE_POST_MEASUREMENT_FRAME",
      "targetLocationId": "probe-pad-j3"
    }
  ]
}
```

## 6. Mechanical Arm Adapter Mock

代码位置：`src/adapters/mockArmController.js`

后续真实机械臂控制器应实现同样输入输出。

#### Input

```json
{
  "id": "step-001",
  "kind": "ARM_MOTION",
  "command": "MOVE_PROBE_TO_LOCATION",
  "targetLocationId": "probe-pad-j3"
}
```

#### Output

```json
{
  "stepId": "step-001",
  "status": "COMPLETED",
  "motionCommand": "MOVE_PROBE_TO_LOCATION",
  "targetLocationId": "probe-pad-j3",
  "precisionMm": 0.03,
  "durationMs": 420
}
```

## 7. Equipment Adapter Mock

代码位置：`src/adapters/mockEquipmentController.js`

后续示波器、电源、万用表等测试设备应实现同样输入输出。

#### Input

```json
{
  "id": "step-002",
  "kind": "EQUIPMENT_MEASUREMENT",
  "instrument": "oscilloscope",
  "signal": "5V_RAIL",
  "locationId": "probe-pad-j3",
  "expectedRange": "4.85V..5.15V"
}
```

#### Output

```json
{
  "stepId": "step-002",
  "status": "COMPLETED",
  "instrument": "oscilloscope",
  "signal": "5V_RAIL",
  "value": 4.98,
  "unit": "V",
  "rippleMvpp": 18.7,
  "pass": true,
  "durationMs": 280
}
```

## 8. Report Generator

代码位置：`src/adapters/reportGenerator.js`

把执行结果转为 GUI 或报告层可直接消费的数据。

#### Input

```json
{
  "run": {
    "runId": "run_mp0teox4_8w7vllfe",
    "input": {
      "command": "Inspect CAN transceiver board and measure 5V rail ripple"
    },
    "execution": {
      "arm": [],
      "equipment": []
    }
  },
  "ragEvidence": [],
  "vlmObservation": {
    "locations": []
  },
  "measurements": [
    {
      "stepId": "step-002",
      "status": "COMPLETED",
      "instrument": "oscilloscope",
      "signal": "5V_RAIL",
      "value": 4.98,
      "unit": "V",
      "rippleMvpp": 18.7,
      "pass": true,
      "durationMs": 280
    }
  ]
}
```

#### Output

```json
{
  "title": "Debugging Bench Agent Test Report",
  "summary": "Bench procedure completed. Mock measurements are within expected limits.",
  "objective": "Inspect CAN transceiver board and measure 5V rail ripple",
  "findings": [
    "VLM detected 2 actionable bench locations.",
    "RAG returned 3 supporting knowledge items.",
    "Execution completed 2 arm actions and 1 equipment measurements."
  ],
  "measurements": [
    {
      "stepId": "step-002",
      "status": "COMPLETED",
      "instrument": "oscilloscope",
      "signal": "5V_RAIL",
      "value": 4.98,
      "unit": "V",
      "rippleMvpp": 18.7,
      "pass": true,
      "durationMs": 280
    }
  ],
  "nextActions": [
    "Replace mock adapters with real VLM/RAG/hardware clients.",
    "Add async job queue before closed-loop hardware execution."
  ]
}
```

## 9. Complete Run Object

Agent 端到端执行完成后，最终对象结构如下。

#### Output

```json
{
  "runId": "run_mp0teox4_8w7vllfe",
  "state": "REPORTING",
  "input": {
    "command": "Inspect CAN transceiver board and measure 5V rail ripple",
    "visualCapture": {
      "imageRef": "Version 1.png",
      "cameraId": "bench-overview"
    },
    "context": {
      "operator": "demo-user",
      "benchId": "kpit-bench-01"
    }
  },
  "plan": {
    "objective": "Inspect CAN transceiver board and measure 5V rail ripple",
    "evidenceIds": ["manual-5v-ripple", "gerber-j3", "procedure-can-u7"],
    "steps": [
      {
        "id": "step-001",
        "kind": "ARM_MOTION",
        "command": "MOVE_PROBE_TO_LOCATION",
        "targetLocationId": "probe-pad-j3"
      },
      {
        "id": "step-002",
        "kind": "EQUIPMENT_MEASUREMENT",
        "instrument": "oscilloscope",
        "signal": "5V_RAIL",
        "locationId": "probe-pad-j3",
        "expectedRange": "4.85V..5.15V"
      },
      {
        "id": "step-003",
        "kind": "VISUAL_CAPTURE",
        "command": "CAPTURE_POST_MEASUREMENT_FRAME",
        "targetLocationId": "probe-pad-j3"
      }
    ]
  },
  "vlmObservation": {
    "model": "mock-vlm-v0",
    "imageRef": "Version 1.png",
    "confidence": 0.86,
    "benchOverview": {
      "boardDetected": true,
      "instrumentsDetected": ["oscilloscope", "programmable-power-supply"],
      "armReachableZones": ["top-left", "center", "probe-pad-j3"]
    },
    "locations": [],
    "recommendedMeasurements": []
  },
  "ragEvidence": [],
  "execution": {
    "arm": [],
    "equipment": []
  },
  "report": {},
  "timeline": [
    {
      "at": "2026-05-11T06:22:42.856Z",
      "state": "PREPARING",
      "message": "Parsed command; querying RAG and VLM."
    },
    {
      "at": "2026-05-11T06:22:42.857Z",
      "state": "EXECUTING",
      "message": "Plan generated; executing mock hardware flow."
    },
    {
      "at": "2026-05-11T06:22:42.857Z",
      "state": "REPORTING",
      "message": "Execution completed; generating report."
    }
  ]
}
```
