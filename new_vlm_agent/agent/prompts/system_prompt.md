## [TOOL_CONSTRAINTS]

Per-phase allowed tools (runtime whitelist; illegal calls are rejected before execution):

### part0_signal_to_tp
- Tools: read_text_file, view_image, search_pdf_text, save_text_file, run_python, list_files

### part0_pdf_search_and_mark
- Tools: search_pdf_text, run_python, mark_tp_on_assembly_from_pdf_hit, pdf_page_to_image, view_image

### partb_locator_largest_ic
- Tools: view_image, save_text_file, run_python, detect_largest_ic_on_assembly_from_vlm_hint, annotate_image, read_text_file
- save_text_file → debug/case10_assembly_vlm_hints.json example:
```json
{
  "approx_bbox_norm": [0.35, 0.40, 0.55, 0.60],
  "region_hint": "largest QFP near green TP, upper-right quadrant",
  "visual_cues": "rectangular plastic package, dense pin rows on four sides"
}
```

### parta_board_largest_ic
- Tools: run_python, detect_largest_ic_on_board_full, detect_largest_ic_on_board_from_vlm_hint, view_image, save_text_file, annotate_image, read_text_file
- Primary path: call `detect_largest_ic_on_board_full` (no VLM hints; auto PCB mask + QFP filters on full board photo).
- Optional: `view_image` on `debug/case10_largest_ic_box.png` after detection for QC.

### partd_case12_align_and_finish
- Tools: case12_build_and_align_from_step02_anchors, emit_step08_from_case12_aligned, run_python, annotate_image, save_text_file, finish, view_image

Invalid tool name or args → standardized JSON error in tool result; fix args and continue (no full-run retry).
