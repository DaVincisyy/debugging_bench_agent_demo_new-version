export function buildModelInputYaml(input) {
  return toYaml({
    Instruction: input.instruction,
    Case_ID: input.caseId,
    Operator: input.operator,
    Input_Assets: {
      Camera_image: fileSummary(input.cameraImage, "camera_image", "PCBA real-world camera image for VLM localization"),
      Bit_image: input.bitImages.map((file) => fileSummary(
        file,
        "bit_image",
        "Board bitmap or placement PDF used to locate component/test-point position"
      )),
      Schematic_Diagram: input.schematicDiagrams.map((file) => fileSummary(
        file,
        "schematic_diagram",
        "Circuit schematic image/PDF used to infer target signal and test point"
      ))
    },
    Model_Attachments: {
      requirement: "Use both bit-map inputs and schematic inputs. PDF files are valid model attachments and should be read together with images.",
      bit_pdf_count: countByMedia(input.bitImages, "pdf"),
      bit_image_count: countByMedia(input.bitImages, "image"),
      schematic_pdf_count: countByMedia(input.schematicDiagrams, "pdf"),
      schematic_image_count: countByMedia(input.schematicDiagrams, "image"),
      attachments: input.modelAttachments.map((file) => fileSummary(file, file.kind, attachmentPurpose(file.kind)))
    },
    Output_Requirement: {
      target: "MG400_xyzr",
      instruction: "Return the MG400 Cartesian target pose for the physical test point found from Camera_image, Bit_image, and Schematic_Diagram.",
      fields: {
        x: { unit: "mm" },
        y: { unit: "mm" },
        z: { unit: "mm" },
        r: { unit: "deg" }
      }
    }
  });
}

function fileSummary(file, role = "attachment", purpose = "") {
  if (!file) {
    return null;
  }

  return {
    role,
    purpose,
    name: file.name,
    type: file.type,
    media_kind: mediaKind(file),
    size: file.size,
    has_data_url: typeof file.dataUrl === "string" && file.dataUrl.length > 0
  };
}

function mediaKind(file) {
  const type = String(file?.type || "").toLowerCase();
  const name = String(file?.name || "").toLowerCase();
  if (type.includes("pdf") || name.endsWith(".pdf")) {
    return "pdf";
  }
  if (type.startsWith("image/")) {
    return "image";
  }
  return "file";
}

function countByMedia(files, kind) {
  return files.filter((file) => mediaKind(file) === kind).length;
}

function attachmentPurpose(kind) {
  if (kind === "bit_image") {
    return "Board bitmap / placement drawing input for the model";
  }
  if (kind === "schematic_diagram") {
    return "Schematic diagram input for the model";
  }
  if (kind === "camera_image") {
    return "Camera image input for VLM localization";
  }
  return "Additional model attachment";
}

function toYaml(value, indent = 0) {
  if (Array.isArray(value)) {
    if (value.length === 0) {
      return "[]";
    }

    return value.map((item) => {
      if (isScalar(item)) {
        return `${spaces(indent)}- ${formatScalar(item)}`;
      }

      const nested = toYaml(item, indent + 2);
      return `${spaces(indent)}-\n${nested}`;
    }).join("\n");
  }

  if (value && typeof value === "object") {
    return Object.entries(value).map(([key, item]) => {
      if (isScalar(item)) {
        return `${spaces(indent)}${key}: ${formatScalar(item)}`;
      }

      return `${spaces(indent)}${key}:\n${toYaml(item, indent + 2)}`;
    }).join("\n");
  }

  return formatScalar(value);
}

function isScalar(value) {
  return value === null || ["string", "number", "boolean"].includes(typeof value);
}

function formatScalar(value) {
  if (value === null || value === undefined) {
    return "null";
  }

  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }

  return JSON.stringify(String(value));
}

function spaces(count) {
  return " ".repeat(count);
}
