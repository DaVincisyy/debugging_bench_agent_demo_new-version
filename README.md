# Debugging Bench Agent Demo

This repository contains a PCBA debugging bench prototype that connects user input, a VLM-based visual reasoning agent, MG400 robot control/simulation, and a small HTTP UI/API for running debugging tasks.

## What Is Included

- `Inputdemo/` - Node.js HTTP service, browser UI, pipeline orchestration, MG400 adapters, VLM task generation, and tests.
- `Vlm agent/Debugging-agent-v2/` - Python VLM agent for locating PCBA test points from board photos, locator diagrams, schematics, and task YAML files.
- `MG400stimulation/` - MG400 simulation experiments and MuJoCo-related workspace analysis tools.
- `mg400demo/` - MG400 TCP/IP control examples and reference scripts.
- `config/` - Shared local configuration such as MG400 connection settings.
- `scripts/` - Utility scripts for service or network testing.

## Architecture

```text
User instruction / uploaded case data
  -> Inputdemo HTTP API and web UI
  -> input parser and task planner
  -> VLM task YAML generation
  -> Debugging-agent-v2 visual reasoning
  -> pixel target / confidence result
  -> MG400 real or simulation execution adapter
  -> run status, report, and UI feedback
```

## Requirements

- Node.js 20 or newer
- npm
- Python 3.10 or newer for the VLM agent and MG400 Python scripts
- Optional: MG400 robot or simulation environment for hardware execution

## Install

Install the Node workspace dependencies from the repository root:

```bash
npm install
```

Install the Python VLM agent dependencies when using `Debugging-agent-v2` directly:

```bash
cd "Vlm agent/Debugging-agent-v2"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Configure VLM Access

Create a local `.env` file in `Vlm agent/Debugging-agent-v2/` and provide the model endpoint:

```env
VLM_BASE_URL=https://api.example.com/v1
VLM_API_KEY=your_api_key
VLM_MODEL=your_vision_model
```

Useful runtime overrides:

- `VLM_AGENT_DIR` - path to `Debugging-agent-v2`.
- `VLM_ENV_FILE` - path to the `.env` file used by the VLM agent.
- `VLM_AGENT_MAX_STEPS` - maximum agent tool-use steps.
- `VLM_AGENT_TIMEOUT_MS` - timeout used by the Node service while waiting for the VLM agent.
- `VLM_AGENT_RUNNER=cli` - force the Node service to use the CLI fallback runner.

## Run The Web/API Service

From the repository root:

```bash
npm start
```

The service starts on `http://localhost:3000` by default.

Available entry points:

- `GET /` - browser UI for submitting debugging tasks.
- `GET /health` - health check.
- `POST /api/runs` - create a debugging run.
- `GET /api/mg400/config` - read MG400 config.
- `POST /api/mg400/config` - update MG400 config.
- `POST /api/mg400/test` - test MG400 connectivity.

## Run Tests

```bash
npm test
```

The Node tests use mock adapters where possible so the pipeline can be checked without calling a real VLM provider or robot.

For the Python VLM agent:

```bash
cd "Vlm agent/Debugging-agent-v2"
python tests\test_unit.py
python tests\test_agent_e2e.py
```

## MG400 Notes

MG400 connection settings live in `config/mg400.json` and `Inputdemo/config/mg400.json`. Keep real network settings aligned with the robot controller before using hardware commands.

The `MG400stimulation/` and `mg400demo/` folders contain simulation, workspace analysis, and TCP/IP examples. Treat hardware-facing scripts carefully and verify robot position, speed, and workspace limits before running motion commands.

## Repository Hygiene

Generated runs, logs, caches, local environments, `node_modules`, packaged VSIX files, and VLM debug workspaces are ignored by `.gitignore`. Keep secrets in local `.env` files only.
