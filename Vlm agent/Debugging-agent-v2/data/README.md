# `data/` — 测试用例目录

## 约定

每组测试 **一个 case 一个子目录**，统一放在 `data/cases/` 下：

```
data/
├─ skills/
│  └─ SKILL.md                          # 全局可复用 skills（唯一）
└─ cases/
   ├─ case_001_tp12_front/              # 任意命名，建议 caseID_描述
   │   ├─ task.yaml                     # 任务说明（必须）
   │   ├─ front_locator_marked.png      # 正面位号图（红框最大芯片 + 绿圈目标点）
   │   ├─ front_board_marked.png        # 正面实物图（红框最大芯片）
   │   ├─ schematic.pdf                 # 原理图 PDF
   │   └─ notes.md                      # 可选：工程师备注
   │
   ├─ case_002_tp07_back/
   │   └─ ...
   │
   └─ ...
```

文件名不必与示例完全一致——`task.yaml` 的 `inputs` 字段告诉 Agent 该去哪找哪一份资料。

## 路径解析规则

`task.yaml` 里 `inputs` 下的**相对路径**会被当作 **相对于 `task.yaml` 自己所在目录**
来解析。所以一个 case 的 yaml 只需要写：

```yaml
inputs:
  locator_image: locator.png        # ← 相对 task.yaml 所在目录
  front_photo: front.jpg
  schematic_text: schematic.txt
```

而不用写 `data/cases/case_001_tp12_front/locator.png`。这样：

- 把一个 case 目录整体拷到别的地方也不会坏。
- 跨 case 复用素材时仍可以写绝对路径 `C:/...`。

## 运行一个 case

```powershell
python -m agent run data/cases/case_001_tp12_front/task.yaml
```

运行产物写在 `workspace/runs/<时间戳>-case_001_tp12_front/`，不会污染 `data/`。

## 批量跑所有 case

```powershell
foreach ($t in Get-ChildItem data/cases -Recurse -Filter task.yaml) {
    python -m agent run $t.FullName --name batch
}
```

或 Linux / macOS：

```bash
for t in data/cases/*/task.yaml; do
    python -m agent run "$t" --name batch
done
```

然后对比 `workspace/runs/*/summary.json` 即可做离线评测 / 换模型对比。
