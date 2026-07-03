"""Fix agent.py to support event_sink parameter (needed by service.py)."""
import paramiko, time

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("139.196.187.182", port=22, username="root", password="Debugagent666", timeout=10)

path = "/opt/vlm-agent/Debugging-agent-v2/agent/agent.py"

# Read the file from server
sftp = client.open_sftp()
with sftp.file(path, "r") as f:
    content = f.read().decode("utf-8")
sftp.close()

changes = 0

# 1. Add event_sink parameter to run()
old1 = (
    "def run(self, question: str,\n"
    "            inputs: dict[str, Any] | None = None,\n"
    "            run_name: str | None = None) -> AgentRun:"
)
new1 = (
    "def run(self, question: str,\n"
    "            inputs: dict[str, Any] | None = None,\n"
    "            run_name: str | None = None,\n"
    "            event_sink: Callable[[str, dict[str, Any]], None] | None = None) -> AgentRun:"
)
if old1 in content:
    content = content.replace(old1, new1)
    changes += 1
    print("1. Added event_sink parameter")
else:
    print("1. event_sink already present or pattern not found")

# 2. Add emit_event helper
old2 = (
    '        self._initial_task_compacted = False\n'
    '        self._run_inputs: dict[str, Any] = {}'
)
if old2 in content:
    new2 = (
        '        self._initial_task_compacted = False\n'
        '        self._run_inputs: dict[str, Any] = {}\n'
        '\n'
        '        def emit_event(event_type: str, payload: dict[str, Any]) -> None:\n'
        '            if event_sink is None:\n'
        '                return\n'
        '            try:\n'
        '                event_sink(event_type, payload)\n'
        '            except Exception:\n'
        '                pass'
    )
    content = content.replace(old2, new2)
    changes += 1
    print("2. Added emit_event helper")

# 3. Add _run_started_at
old3 = "        self._run_started_at = time.time()"
if old3 not in content:
    # Add it before the phase_steps line
    old_target = "        phase_steps: dict[str, list[AgentStep]] = {}"
    if old_target in content:
        content = content.replace(old_target, f"        self._run_started_at = time.time()\n{old_target}")
        changes += 1
        print("3. Added _run_started_at")
else:
    print("3. _run_started_at already present")

# 4. Add emit_event calls throughout the loop
if 'emit_event("agent.run_dir"' not in content:
    old4 = (
        '        self.console.print(Panel.fit(\n'
        '            f"[bold]model[/bold] = {self.cfg.model}\\\\n"'
    )
    # More robust: find the console.print line before run_dir
    if 'self.console.print(Panel.fit(' in content:
        indicator = 'self.log.info("Program start run_dir=%s workflow=%s",'
        old4_real = "        self.console.print(Panel.fit("
        if old4_real in content:
            emit_code = (
                '        emit_event("agent.run_dir", {"run_dir": str(run_dir)})\n'
                '        self.log.info("Program start run_dir=%s workflow=%s",'
            )
            # Find the right pattern
            if emit_code not in content:
                # Try: add emit before console.print near run_dir
                target4 = (
                    '        self.log.info("Program start run_dir=%s workflow=%s",'
                )
                if target4 in content:
                    content = content.replace(
                        target4,
                        f'        emit_event("agent.run_dir", {{"run_dir": str(run_dir)}})\n{target4}'
                    )
                    changes += 1
                    print("4. Added emit_event(agent.run_dir)")

# 5. Add emit_event calls for step results
if 'emit_event("agent.waiting"' in content:
    print("5. emit_event calls already present")
elif 'self.log.info("Step start step=%s", step_idx)' in content:
    # Add waiting event before step
    old5 = 'self.log.info("Step start step=%s", step_idx)'
    new5 = (
        'emit_event("agent.waiting", {"step": step_idx})\n'
        '                self.log.info("Step start step=%s", step_idx)'
    )
    content = content.replace(old5, new5)
    changes += 1
    print("5. Added emit_event(agent.waiting)")

# 6. Add emit_event(agent.step) for non-tool-call steps
if 'emit_event("agent.step"' not in content:
    # Find the path where no tool calls exist
    indicator6 = 'self._render_step_timing(step_idx, step_timing, [])'
    if indicator6 in content:
        new6 = (
            'emit_event("agent.step", {\n'
            '                        "step": step_idx,\n'
            '                        "tool_calls": [],\n'
            '                        "tool_results": [],\n'
            '                        "final": False,\n'
            '                    })\n'
            '                    self._render_step_timing(step_idx, step_timing, [])'
        )
        content = content.replace(indicator6, new6)
        changes += 1
        print("6. Added emit_event(agent.step)")

# 7. Add emit_event for final/failed
if 'emit_event("agent.final"' not in content:
    # Find the finish-tool-called section
    old7 = 'result.stopped_reason = "finish-tool-called"'
    if old7 in content:
        new7 = (
            'result.stopped_reason = "finish-tool-called"\n'
            '                    emit_event("agent.final", {\n'
            '                        "final_answer": final_answer,\n'
            '                        "stopped_reason": result.stopped_reason,\n'
            '                        "run_dir": str(run_dir),\n'
            '                    })'
        )
        content = content.replace(old7, new7)
        changes += 1
        print("7. Added emit_event(agent.final)")

# Also add emit_event for tool-call step
if 'emit_event("agent.step", {\n                    "step": step_idx,\n                    "tool_calls":' not in content:
    old8 = (
        'self._render_step_timing(step_idx, step_timing, tool_timing_payload)\n'
        '                emit_event("agent.step", {'
    )
    if old8 not in content:
        # Find the existing tool-step render_timing
        indicator8 = 'self._render_step_timing(step_idx, step_timing, tool_timing_payload)'
        if indicator8 in content:
            new8 = (
                'self._render_step_timing(step_idx, step_timing, tool_timing_payload)\n'
                '                emit_event("agent.step", {\n'
                '                    "step": step_idx,\n'
                '                    "tool_calls": tool_call_payload,\n'
                '                    "tool_results": tool_result_payload,\n'
                '                    "final": final_answer is not None,\n'
                '                })'
            )
            content = content.replace(indicator8, new8)
            changes += 1
            print("8. Added emit_event(agent.step) for tool calls")

if 'emit_event("agent.failed"' not in content:
    old9 = 'result.stopped_reason = f"exception:{type(e).__name__}"'
    if old9 in content:
        new9 = (
            'result.stopped_reason = f"exception:{type(e).__name__}"\n'
            '            result.last_error = str(e)[:8000]\n'
            '            emit_event("agent.failed", {\n'
            '                "error": result.last_error,\n'
            '                "stopped_reason": result.stopped_reason,\n'
            '                "run_dir": str(run_dir),\n'
            '            })'
        )
        content = content.replace(old9, new9)
        # Remove the duplicate result.last_error line
        content = content.replace(
            "result.last_error = str(e)[:8000]\n            result.last_error = str(e)[:8000]",
            "result.last_error = str(e)[:8000]"
        )
        changes += 1
        print("9. Added emit_event(agent.failed)")

# Write back
sftp = client.open_sftp()
with sftp.file(path, "w") as f:
    f.write(content)
sftp.close()

print(f"Total changes: {changes}")

# Verify syntax
stdin, stdout, stderr = client.exec_command(
    'cd /opt/vlm-agent/Debugging-agent-v2 && '
    '.venv/bin/python -c "compile(open(\"agent/agent.py\").read(), \"agent.py\", \"exec\"); print(\"Syntax OK\")" 2>&1'
)
out = stdout.read().decode().strip()
err = stderr.read().decode().strip()
print("Syntax:", out or err)

# Commit + push + wait for deploy
if changes > 0:
    stdin, stdout, stderr = client.exec_command(
        'cd /opt/vlm-agent/Debugging-agent-v2 && '
        'git add agent/agent.py && '
        'git commit -m "Fix: restore event_sink support for service.py compatibility" && '
        'git push origin master 2>&1'
    )
    print("Push:", (stdout.read().decode() + stderr.read().decode()).strip()[-200:])

    time.sleep(6)

stdin, stdout, stderr = client.exec_command("curl -s http://127.0.0.1:8000/health")
print("Health:", stdout.read().decode().strip())

client.close()
print("Done")
