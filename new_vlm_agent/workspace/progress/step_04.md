## Step 4 — 双 ROI 裁切

**Board ROI**：`debug/step04_roi_crop.png`
- bbox: [1959, 1109, 2259, 1409]
- 名义尺寸: 300x300, 实际: 300x300
- 锚点: tp_prior_board (2109, 1259)

**Locator ROI**：`debug/step04_locator_roi_crop.png`
- bbox: [2276, 1458, 2576, 1758]
- 名义尺寸: 300x300, 实际: 300x300
- 锚点: TP1 绿圈中心 (2423, 1608)

**同尺寸声明**：两 ROI 均为 300x300 像素，便于并排对照。

**路径 A 结论**：VLM 结构对照确认目标焊盘位置，approx (115, 210) 经 snap 修正为 (100, 212)，qc_direct_path=PASS。
