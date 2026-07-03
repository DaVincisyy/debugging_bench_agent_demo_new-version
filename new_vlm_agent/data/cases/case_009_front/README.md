# case_009_tp809_front

全流程 Step1–8 试跑。**Step1 不预设 TP**：由 `user_measurement_question`（自然语言）+ 原理图 PNG 推断 `TPxxx`，再用位号图 PDF 画绿圈。

| 文件 | 说明 |
| --- | --- |
| `位号图7.29.pdf` | 位号图 PDF（`search_pdf_text` + `rect_pdf`） |
| `PCBA_IMG.jpg` | 正面实物照片 |
| `voyah_hvac_v01_20240729_09.png` | 原理图 PNG（`view_image`，勿对原理图 `search_pdf_text`） |
| `task.yaml` | 含 `user_measurement_question` 等 Step1 输入 |

运行：

```powershell
python -m agent run data/cases/case_009_tp809_front/task.yaml
```

Step1 绿圈输出（语义 `front_locator_marked`）：`debug/step01_locator_front_anchor.png`（相对 workspace）。详见 `../../skills/SKILL.md`。
