# case_002_tp332_front

示例 case 骨架。需要你自己往这里塞四份素材：

| 文件 | 说明 |
| --- | --- |
| `locator.png` | 位号图（silk-screen 图）并用红色圆圈等方式标出了要测的 TP。 |
| `schematic.txt` | 原理图的 netlist 文本（可以是从 CAD 导出的 .net / .cir / .txt）。 |
| `front.jpg` | Camera 拍摄的板子**正面**照片。 |
| `back.jpg` | Camera 拍摄的**反面**照片，可选；如果没有，请在 `task.yaml` 里删掉 `back_photo` 那一行。 |

运行：

```powershell
python -m agent run data/cases/case_002_tp332_front/task.yaml
```

