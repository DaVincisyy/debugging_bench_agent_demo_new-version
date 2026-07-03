# Step1A: Schematic analysis for 5V_1 test point

## User question
我要测量 5V_1 电源网络的滤波后电压，应该用哪个测试点？

## Schematic inspection
- Schematic file: voyah_hvac_v01_20240729_09.png
- Multiple 5V_1 nodes present in the schematic.

## Candidate test points near 5V_1
1. **TP809** - Located on the 5V_1 rail after L803 (22uH inductor) and before C806-C810 capacitors. This is the main 5V_1 output node.
2. **TP810** - Located on a 5V_1 rail connected to U801 (EPAD), but this is an internal supply, not the main filtered output.
3. **TP822** - Located on a 5V_1 rail connected to R815, but this is a different branch.

## Decision
**TP809** is the most appropriate test point for measuring the filtered 5V_1 voltage because:
- It is on the main 5V_1 output path.
- It is located after the inductor L803 and before the output capacitors, representing the filtered voltage.
- It is clearly labeled as a test point on the 5V_1 rail.

## Conclusion
The test point to use is **TP809**.