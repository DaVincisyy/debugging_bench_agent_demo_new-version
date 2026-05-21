# case_001_tp12_front

这个 case 现在用于 **Step1-8 全流程** 调试（不再默认从 Step3 起跑）。

请准备 3 份原始输入：

| 文件 | 说明 |
| --- | --- |
| `位号图_front_back.pdf` | 无标记位号图 PDF（两页，front/back 各一页）。 |
| `board_front.jpg` | 板子正面实物图（无标记）。 |
| `schematic.pdf` | 该板原理图 PDF，用于 signal -> TP 推断。 |

并在 `task.yaml` 中设置：
- `target_signal`（例如 `TP12` 或具体 net 名）；
- `locator_front_page` / `locator_back_page`（默认 1/2）。

运行：

```powershell
python -m agent run data/cases/case_001_tp12_front/task.yaml
```

全流程关键产物：
- Step1: `debug/step01_locator_front_marked.png`
- Step2: `debug/step02_locator_front_anchor.png`, `debug/step02_board_front_anchor.png`
- Step3-8: `debug/step03_mapping.json`, `debug/step08_final_tp.png` 等

想多加 case 就复制本目录，改成 `case_002_xxx/` 即可。
