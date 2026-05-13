from __future__ import annotations

import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, Preformatted, SimpleDocTemplate, Spacer


WORKSPACE = Path(r"C:\Users\FX506L\OneDrive\文档\New project 2")
PROJECT = Path(r"C:\Users\FX506L\Desktop\debugging_bench_agent_demo")
OUTPUT = WORKSPACE / "output" / "pdf" / "debugging_bench_agent_demo_vlm_json.pdf"


def register_font() -> str:
    candidates = [
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\simsun.ttc"),
        Path(r"C:\Windows\Fonts\arial.ttf"),
    ]
    for font_path in candidates:
        if font_path.exists():
            pdfmetrics.registerFont(TTFont("DocFont", str(font_path)))
            return "DocFont"
    return "Helvetica"


def pretty(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def build_vlm_request(run_id: str, images: list[dict], test_info: dict, metadata: dict | None = None) -> dict:
    metadata = metadata or {}
    return {
        "run_id": run_id,
        "images": [
            {
                "image_id": image["image_id"],
                "path": image["path"],
                "mime_type": image["mime_type"],
            }
            for image in images
        ],
        "targets": [
            {
                "signal": signal_name,
                "expected_label": signal_info["test_point"],
                "component_hint": test_info["component"],
            }
            for signal_name, signal_info in test_info["signals"].items()
        ],
        "output_format": {
            "need_pixel_coordinate": True,
            "need_robot_coordinate": True,
            "need_bbox": True,
            "need_confidence": True,
        },
        "calibration": {
            "coordinate_frame": metadata.get("coordinate_frame", "robot"),
            "calibration_id": metadata.get("calibration_id", "default"),
        },
    }


def locate_test_points(test_info: dict) -> dict:
    located_points = {}
    for signal_name, signal_info in test_info["signals"].items():
        located_points[signal_name] = {
            "test_point": signal_info["test_point"],
            "pin": signal_info["pin"],
            "expected_voltage": signal_info["expected_voltage"],
            "pixel_coordinate": signal_info["pixel_coordinate"],
            "robot_coordinate": signal_info["robot_coordinate"],
            "mock_measured_voltage": signal_info.get("mock_measured_voltage"),
            "mock_result": signal_info.get("mock_result", "PASS"),
        }
    return located_points


def with_vlm_evidence(located_points: dict, image_id: str) -> dict:
    enriched = {}
    for signal_name, point_info in located_points.items():
        x, y = point_info["pixel_coordinate"]
        enriched[signal_name] = {
            **point_info,
            "confidence": 0.92,
            "evidence": {
                "image_id": image_id,
                "bbox": [x - 20, y - 20, x + 20, y + 20],
            },
        }
    return enriched


def make_test_info(catalog: dict, interface_name: str) -> dict:
    source = catalog["interfaces"][interface_name]
    return {
        "project": catalog["project"],
        "interface": interface_name,
        "display_name": source["display_name"],
        "component": source["component"],
        "report_file": source["report_file"],
        "signals": source["signals"],
    }


def add_code(story: list, title: str, body: str, styles: dict) -> None:
    story.append(Paragraph(title, styles["Heading2"]))
    story.append(Preformatted(body, styles["Code"]))
    story.append(Spacer(1, 7 * mm))


def draw_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont(doc.font_name, 8)
    canvas.setFillColor(colors.HexColor("#666666"))
    canvas.drawRightString(A4[0] - 15 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def main() -> None:
    font_name = register_font()
    catalog = json.loads((PROJECT / "data" / "interface_test_catalog.json").read_text(encoding="utf-8"))
    test_log_path = PROJECT / "output" / "test_log.json"
    test_log = json.loads(test_log_path.read_text(encoding="utf-8")) if test_log_path.exists() else {}

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    base_styles = getSampleStyleSheet()
    styles = {
        "Title": ParagraphStyle(
            "Title",
            parent=base_styles["Title"],
            fontName=font_name,
            fontSize=18,
            leading=24,
            spaceAfter=10,
            textColor=colors.HexColor("#1f2937"),
        ),
        "Heading1": ParagraphStyle(
            "Heading1",
            parent=base_styles["Heading1"],
            fontName=font_name,
            fontSize=13,
            leading=17,
            spaceBefore=8,
            spaceAfter=5,
            textColor=colors.HexColor("#111827"),
        ),
        "Heading2": ParagraphStyle(
            "Heading2",
            parent=base_styles["Heading2"],
            fontName=font_name,
            fontSize=10,
            leading=13,
            spaceBefore=5,
            spaceAfter=3,
            textColor=colors.HexColor("#374151"),
        ),
        "Body": ParagraphStyle(
            "Body",
            parent=base_styles["BodyText"],
            fontName=font_name,
            fontSize=9,
            leading=13,
            alignment=TA_LEFT,
            spaceAfter=5,
        ),
        "Code": ParagraphStyle(
            "Code",
            fontName=font_name,
            fontSize=7,
            leading=8.6,
            leftIndent=4 * mm,
            rightIndent=2 * mm,
            borderWidth=0.35,
            borderColor=colors.HexColor("#d1d5db"),
            borderPadding=5,
            backColor=colors.HexColor("#f8fafc"),
            splitLongWords=True,
        ),
    }

    doc = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        rightMargin=13 * mm,
        leftMargin=13 * mm,
        topMargin=14 * mm,
        bottomMargin=16 * mm,
    )
    doc.font_name = font_name

    story = [
        Paragraph("Debugging Bench Agent Demo - VLM 输入/输出 JSON 汇总", styles["Title"]),
        Paragraph(
            "本 PDF 汇总项目中所有与 VLM 交互相关的 JSON：API 层传给 VLM 的 request、"
            "VLM mock 的直接输入 test_info，以及 VLM 返回的 located_points / evidence 结果。",
            styles["Body"],
        ),
        Paragraph(f"源项目：{PROJECT}", styles["Body"]),
        Paragraph(f"数据来源：controllers/multimodal_controller.py、modules/vlm_mock.py、data/interface_test_catalog.json、output/test_log.json", styles["Body"]),
        Spacer(1, 4 * mm),
    ]

    sample_image = {
        "image_id": "image_demo_001",
        "path": "uploads/20260512-143000-demo/image_demo_001.jpg",
        "mime_type": "image/jpeg",
    }

    for interface_name in ["CAN", "LIN"]:
        test_info = make_test_info(catalog, interface_name)
        located = locate_test_points(test_info)
        vlm_request = build_vlm_request(
            run_id=f"20260512-143000-{interface_name.lower()}",
            images=[sample_image],
            test_info=test_info,
            metadata={"coordinate_frame": "robot", "calibration_id": "default"},
        )
        vlm_result = with_vlm_evidence(located, sample_image["image_id"])

        story.append(Paragraph(f"{interface_name} 接口 VLM JSON", styles["Heading1"]))
        add_code(story, "1. API 层传给 VLM 的 JSON - details.vlm_request / vlm_request", pretty(vlm_request), styles)
        add_code(story, "2. VLM mock 直接输入 JSON - locate_test_points(test_info)", pretty(test_info), styles)
        add_code(story, "3. VLM mock 原始输出 JSON - located_points", pretty(located), styles)
        add_code(story, "4. API 返回中的 VLM 输出 JSON - details.vlm_result / located_points with evidence", pretty(vlm_result), styles)
        if interface_name == "CAN" and test_log:
            add_code(story, "5. 当前 output/test_log.json 中记录的 VLM 输出 - located_points", pretty(test_log.get("located_points", {})), styles)
        if interface_name == "CAN":
            story.append(PageBreak())

    story.append(Paragraph("接口位置说明", styles["Heading1"]))
    story.append(Paragraph("/api/v1/test-runs：统一测试入口，返回 details.vlm_request 和 details.vlm_result。", styles["Body"]))
    story.append(Paragraph("/api/v1/vlm/locate-test-points：独立 VLM 定位入口，返回 vlm_request 和 located_points。", styles["Body"]))
    story.append(Paragraph("main.py 文本兼容流程：没有独立 vlm_request 字段，但 VLM mock 的直接输入为 test_info，输出为 located_points。", styles["Body"]))

    doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    print(OUTPUT)


if __name__ == "__main__":
    main()
