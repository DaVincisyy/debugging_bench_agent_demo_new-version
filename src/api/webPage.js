export const webPage = String.raw`<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Debugging Bench Agent</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f8fb;
      --panel: #ffffff;
      --text: #18202f;
      --muted: #667085;
      --line: #d7dce5;
      --primary: #1769aa;
      --primary-dark: #0f4f83;
      --accent: #2f8f7b;
      --danger: #b42318;
      --shadow: 0 10px 28px rgba(16, 24, 40, 0.08);
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      font-family: "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
      background: var(--bg);
      color: var(--text);
    }

    header {
      border-bottom: 1px solid var(--line);
      background: #fff;
    }

    .topbar {
      max-width: 1180px;
      margin: 0 auto;
      padding: 18px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 20px;
    }

    h1 {
      margin: 0;
      font-size: 22px;
      line-height: 1.25;
      font-weight: 650;
    }

    main {
      max-width: 1180px;
      margin: 0 auto;
      padding: 28px 24px 44px;
      display: grid;
      grid-template-columns: minmax(0, 1.05fr) minmax(340px, 0.95fr);
      gap: 22px;
    }

    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }

    .panel {
      padding: 20px;
    }

    .stack {
      display: grid;
      gap: 18px;
    }

    h2 {
      margin: 0 0 12px;
      font-size: 16px;
      font-weight: 650;
    }

    label {
      display: block;
      margin-bottom: 8px;
      font-size: 13px;
      font-weight: 600;
      color: #344054;
    }

    textarea,
    input[type="text"],
    select {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 11px 12px;
      font: inherit;
      color: var(--text);
      background: #fff;
    }

    textarea {
      min-height: 118px;
      resize: vertical;
    }

    input[type="file"] {
      width: 100%;
      min-height: 44px;
      border: 1px dashed #a9b4c5;
      border-radius: 6px;
      padding: 10px;
      background: #fbfcfe;
      color: var(--muted);
    }

    .segmented {
      display: inline-grid;
      grid-template-columns: 1fr 1fr;
      border: 1px solid var(--line);
      border-radius: 6px;
      overflow: hidden;
      background: #fff;
    }

    .segmented button {
      border: 0;
      padding: 9px 14px;
      background: transparent;
      color: var(--muted);
      font: inherit;
      cursor: pointer;
    }

    .segmented button.active {
      background: var(--primary);
      color: #fff;
    }

    .hidden {
      display: none;
    }

    .grid-2 {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 14px;
    }

    .hint {
      margin: 7px 0 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }

    .file-list {
      display: grid;
      gap: 8px;
      margin-top: 10px;
    }

    .file-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: center;
      padding: 9px 10px;
      border: 1px solid #e5e8ef;
      border-radius: 6px;
      background: #fcfcfd;
      font-size: 13px;
    }

    .file-row span {
      overflow-wrap: anywhere;
    }

    .badge {
      color: #0f5132;
      background: #e8f5ef;
      border: 1px solid #b7dfcc;
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 12px;
      white-space: nowrap;
    }

    .actions {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 12px;
      padding-top: 2px;
    }

    .voice-tools {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 10px;
      margin: 10px 0 12px;
    }

    .secondary {
      border: 1px solid var(--line);
      border-radius: 6px;
      min-height: 38px;
      padding: 0 13px;
      background: #fff;
      color: var(--text);
      font: inherit;
      font-weight: 600;
      cursor: pointer;
    }

    .secondary:hover {
      border-color: var(--primary);
      color: var(--primary);
    }

    .secondary.recording {
      border-color: #b42318;
      color: #b42318;
      background: #fff4f2;
    }

    .primary {
      border: 0;
      border-radius: 6px;
      min-height: 42px;
      padding: 0 18px;
      background: var(--primary);
      color: #fff;
      font: inherit;
      font-weight: 650;
      cursor: pointer;
    }

    .primary:hover {
      background: var(--primary-dark);
    }

    .primary:disabled {
      cursor: progress;
      opacity: 0.68;
    }

    .status {
      color: var(--muted);
      font-size: 13px;
    }

    .status.error {
      color: var(--danger);
    }

    .result {
      min-height: 560px;
      overflow: hidden;
    }

    .empty {
      color: var(--muted);
      border: 1px dashed var(--line);
      border-radius: 6px;
      padding: 22px;
      line-height: 1.6;
      background: #fbfcfe;
    }

    .kv {
      display: grid;
      gap: 10px;
      margin-bottom: 16px;
    }

    .kv div {
      display: grid;
      grid-template-columns: 110px minmax(0, 1fr);
      gap: 12px;
      font-size: 13px;
    }

    .kv dt {
      color: var(--muted);
    }

    .kv dd {
      margin: 0;
      overflow-wrap: anywhere;
    }

    pre {
      margin: 0;
      max-height: 390px;
      overflow: auto;
      padding: 14px;
      border-radius: 6px;
      background: #101828;
      color: #f2f4f7;
      font-size: 12px;
      line-height: 1.5;
    }

    @media (max-width: 860px) {
      main {
        grid-template-columns: 1fr;
        padding: 18px 14px 30px;
      }

      .topbar {
        padding: 16px 14px;
      }

      .grid-2 {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <header>
    <div class="topbar">
      <h1>Debugging Bench Agent</h1>
      <span class="status">VLM + 大模型输入工作台</span>
    </div>
  </header>

  <main>
    <section class="panel">
      <form id="run-form" class="stack">
        <div>
          <h2>1. Prompt</h2>
          <div class="segmented" role="tablist" aria-label="Prompt type">
            <button type="button" id="text-mode" class="active">文字</button>
            <button type="button" id="voice-mode">语音</button>
          </div>
        </div>

        <div id="text-prompt">
          <label for="command">工程师指令</label>
          <textarea id="command" placeholder="例如：我要确认输入电压 VCP 是否正常"></textarea>
          <p class="hint">这段文字会作为本次任务的 user prompt，并参与测试点识别。</p>
        </div>

        <div id="voice-prompt" class="hidden">
          <label for="voiceTranscript">语音转文字</label>
          <textarea id="voiceTranscript" placeholder="点击开始听写后，说出工程师指令"></textarea>
          <div class="voice-tools">
            <button type="button" id="startVoice" class="secondary">开始听写</button>
            <button type="button" id="stopVoice" class="secondary" disabled>停止</button>
            <span id="voiceStatus" class="status">识别语言：中文普通话</span>
          </div>
          <p class="hint">浏览器会把当前语音实时转成文字，转写结果会作为本次 prompt。</p>
        </div>

        <div>
          <h2>2. 上传给 VLM 的图片</h2>
          <label for="vlmImage">实物图 / Bench 图像</label>
          <input id="vlmImage" type="file" accept="image/*">
          <p class="hint">例如 PCBA_IMG.jpg 或现场相机帧，用于 VLM 识别实物位置。</p>
          <div id="vlmImageList" class="file-list"></div>
        </div>

        <div>
          <h2>3. 上传给大模型的图纸与位图</h2>
          <div class="grid-2">
            <div>
              <label for="schematicFiles">原理图 / 原理图位图</label>
              <input id="schematicFiles" type="file" accept="image/*,.pdf" multiple>
            </div>
            <div>
              <label for="layoutFiles">位号图 / 位号图位图</label>
              <input id="layoutFiles" type="file" accept="image/*,.pdf" multiple>
            </div>
          </div>
          <p class="hint">RAG 暂时不参与，这些 PDF/PNG/JPG 会作为模型输入附件，用来唯一确定 TP 编号和位置。</p>
          <div id="attachmentList" class="file-list"></div>
        </div>

        <div>
          <h2>4. Case 信息</h2>
          <div class="grid-2">
            <div>
              <label for="caseId">Case ID</label>
              <input id="caseId" type="text" placeholder="case-001">
            </div>
            <div>
              <label for="operator">Operator</label>
              <input id="operator" type="text" placeholder="demo-user">
            </div>
          </div>
        </div>

        <div class="actions">
          <span id="status" class="status"></span>
          <button id="submit" class="primary" type="submit">运行 Agent</button>
        </div>
      </form>
    </section>

    <section class="panel result">
      <h2>运行结果</h2>
      <div id="result" class="empty">提交后会显示 runId、状态、VLM 输入摘要、模型附件摘要和 mock 执行报告。</div>
    </section>
  </main>

  <script>
    const form = document.querySelector("#run-form");
    const statusEl = document.querySelector("#status");
    const resultEl = document.querySelector("#result");
    const submitEl = document.querySelector("#submit");
    const textMode = document.querySelector("#text-mode");
    const voiceMode = document.querySelector("#voice-mode");
    const textPrompt = document.querySelector("#text-prompt");
    const voicePrompt = document.querySelector("#voice-prompt");
    const commandEl = document.querySelector("#command");
    const voiceTranscriptEl = document.querySelector("#voiceTranscript");
    const startVoiceEl = document.querySelector("#startVoice");
    const stopVoiceEl = document.querySelector("#stopVoice");
    const voiceStatusEl = document.querySelector("#voiceStatus");
    const vlmImageEl = document.querySelector("#vlmImage");
    const schematicFilesEl = document.querySelector("#schematicFiles");
    const layoutFilesEl = document.querySelector("#layoutFiles");
    const vlmImageListEl = document.querySelector("#vlmImageList");
    const attachmentListEl = document.querySelector("#attachmentList");
    let promptMode = "text";
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    const recognizer = SpeechRecognition ? new SpeechRecognition() : null;
    let finalTranscript = "";

    if (recognizer) {
      recognizer.lang = "zh-CN";
      recognizer.continuous = true;
      recognizer.interimResults = true;

      recognizer.onstart = () => {
        startVoiceEl.disabled = true;
        stopVoiceEl.disabled = false;
        startVoiceEl.classList.add("recording");
        voiceStatusEl.textContent = "正在听写...";
      };

      recognizer.onresult = (event) => {
        let interimTranscript = "";
        for (let index = event.resultIndex; index < event.results.length; index += 1) {
          const transcript = event.results[index][0].transcript;
          if (event.results[index].isFinal) {
            finalTranscript += transcript.trim() + " ";
          } else {
            interimTranscript += transcript;
          }
        }
        voiceTranscriptEl.value = (finalTranscript + interimTranscript).trim();
        commandEl.value = voiceTranscriptEl.value;
      };

      recognizer.onerror = (event) => {
        voiceStatusEl.textContent = "听写失败：" + event.error;
      };

      recognizer.onend = () => {
        startVoiceEl.disabled = false;
        stopVoiceEl.disabled = true;
        startVoiceEl.classList.remove("recording");
        if (voiceTranscriptEl.value.trim()) {
          voiceStatusEl.textContent = "听写已停止";
        } else {
          voiceStatusEl.textContent = "识别语言：中文普通话";
        }
      };
    } else {
      startVoiceEl.disabled = true;
      stopVoiceEl.disabled = true;
      voiceStatusEl.textContent = "当前浏览器不支持实时语音识别";
    }

    function setPromptMode(mode) {
      promptMode = mode;
      textMode.classList.toggle("active", mode === "text");
      voiceMode.classList.toggle("active", mode === "voice");
      textPrompt.classList.toggle("hidden", mode !== "text");
      voicePrompt.classList.toggle("hidden", mode !== "voice");
    }

    function fileSize(bytes) {
      if (!bytes) return "0 B";
      const units = ["B", "KB", "MB", "GB"];
      const power = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
      return (bytes / Math.pow(1024, power)).toFixed(power ? 1 : 0) + " " + units[power];
    }

    function renderFiles(target, files, kind) {
      target.innerHTML = "";
      Array.from(files).forEach((file) => {
        const row = document.createElement("div");
        row.className = "file-row";
        row.innerHTML = "<span>" + file.name + "</span><strong class=\"badge\">" + kind + " · " + fileSize(file.size) + "</strong>";
        target.appendChild(row);
      });
    }

    function readFile(file) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve({
          name: file.name,
          type: file.type || "application/octet-stream",
          size: file.size,
          dataUrl: reader.result
        });
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
      });
    }

    async function collectAttachments(files, kind) {
      const entries = [];
      for (const file of Array.from(files)) {
        entries.push({ kind, ...(await readFile(file)) });
      }
      return entries;
    }

    function renderResult(run) {
      const attachmentCount = run.input.modelAttachments?.length || 0;
      const imageRef = run.vlmObservation?.imageRef || "-";
      resultEl.className = "";
      resultEl.innerHTML =
        "<dl class=\"kv\">" +
          "<div><dt>Run ID</dt><dd>" + run.runId + "</dd></div>" +
          "<div><dt>State</dt><dd>" + run.state + "</dd></div>" +
          "<div><dt>VLM Image</dt><dd>" + imageRef + "</dd></div>" +
          "<div><dt>模型附件</dt><dd>" + attachmentCount + " 个</dd></div>" +
          "<div><dt>报告</dt><dd>" + (run.report?.summary || "-") + "</dd></div>" +
        "</dl>" +
        "<pre>" + JSON.stringify(run, null, 2) + "</pre>";
    }

    textMode.addEventListener("click", () => setPromptMode("text"));
    voiceMode.addEventListener("click", () => setPromptMode("voice"));
    voiceTranscriptEl.addEventListener("input", () => {
      commandEl.value = voiceTranscriptEl.value;
    });
    startVoiceEl.addEventListener("click", () => {
      if (!recognizer) return;
      finalTranscript = voiceTranscriptEl.value.trim();
      finalTranscript = finalTranscript ? finalTranscript + " " : "";
      recognizer.start();
    });
    stopVoiceEl.addEventListener("click", () => {
      if (recognizer) recognizer.stop();
    });
    vlmImageEl.addEventListener("change", () => renderFiles(vlmImageListEl, vlmImageEl.files, "VLM"));
    schematicFilesEl.addEventListener("change", () => {
      const files = [...schematicFilesEl.files, ...layoutFilesEl.files];
      renderFiles(attachmentListEl, files, "模型输入");
    });
    layoutFilesEl.addEventListener("change", () => {
      const files = [...schematicFilesEl.files, ...layoutFilesEl.files];
      renderFiles(attachmentListEl, files, "模型输入");
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      statusEl.className = "status";
      statusEl.textContent = "正在读取文件...";
      submitEl.disabled = true;

      try {
        const textCommand = promptMode === "voice" ? voiceTranscriptEl.value.trim() : commandEl.value.trim();
        if (promptMode === "text" && !textCommand) {
          throw new Error("请填写文字 prompt。");
        }
        if (promptMode === "voice" && !textCommand) {
          throw new Error("请先完成语音转文字，或手动填写转写结果。");
        }

        const vlmImage = vlmImageEl.files[0] ? await readFile(vlmImageEl.files[0]) : null;
        const modelAttachments = [
          ...(await collectAttachments(schematicFilesEl.files, "schematic")),
          ...(await collectAttachments(layoutFilesEl.files, "layout"))
        ];

        const payload = {
          prompt: {
            mode: promptMode,
            text: textCommand
          },
          command: textCommand,
          visualCapture: vlmImage ? {
            imageRef: vlmImage.name,
            image: vlmImage,
            target: "vlm"
          } : null,
          modelAttachments,
          context: {
            caseId: document.querySelector("#caseId").value.trim(),
            operator: document.querySelector("#operator").value.trim()
          }
        };

        statusEl.textContent = "正在运行...";
        const response = await fetch("/api/runs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || "请求失败。");
        }
        renderResult(data);
        statusEl.textContent = "完成";
      } catch (error) {
        statusEl.className = "status error";
        statusEl.textContent = error.message;
      } finally {
        submitEl.disabled = false;
      }
    });
  </script>
</body>
</html>`;
