## Step 05 - Candidate Detection & Scoring

**Method**: Path A (dual_roi_direct_vlm + snap_nearest_circle)

**Candidates detected**: 28 circular pads in ROI

**Winner**: c15
- ROI coords: (156.5, 150.5)
- Global coords: (1713, 387)
- r_vis: 12.1
- circularity: 0.889
- dist_to_prior: 6.5 px

**Selection rationale**: 路径 A dual_roi_direct_vlm + snap_nearest_circle 选中右列第 3 个焊盘 (TP415)，与先验位置偏差 7px，圆度 0.889。候选评分基于圆度 (0.5) + 距先验距离 (0.5)。

**candidate c15 score**: 0.94 (circularity 0.889 * 0.5 + proximity 0.935 * 0.5)
