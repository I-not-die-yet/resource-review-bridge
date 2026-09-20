"""Minimal Codex app-server JSONL client."""

import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import time
from typing import Any, Dict, Optional


class AppServerError(RuntimeError):
    pass


class AppServerClient:
    def __init__(self, command: Optional[str] = None, timeout_seconds: int = 180):
        self.command = command or os.environ.get("RESOURCE_REVIEW_CODEX")
        self.timeout_seconds = timeout_seconds

    def _resolve_command(self) -> str:
        if self.command:
            resolved = shutil.which(self.command)
            if resolved:
                return resolved
            candidate = Path(self.command).expanduser()
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
            raise AppServerError("Codex app-server executable is unavailable")

        resolved = shutil.which("codex")
        if resolved:
            return resolved

        candidates = (
            Path.home() / "Desktop/ChatGPT.app/Contents/Resources/codex",
            Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
            Path.home() / "Applications/ChatGPT.app/Contents/Resources/codex",
        )
        for candidate in candidates:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        raise AppServerError("Codex app-server executable is unavailable")

    def run(self, prompt: str, cwd: str, output_schema: Dict[str, Any], local_images=None) -> Dict[str, Any]:
        command = self._resolve_command()
        try:
            proc = subprocess.Popen(
                [command, "app-server", "--listen", "stdio://"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise AppServerError("Codex app-server could not be started") from exc
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)
        sequence = 0
        thread_id = None
        final_text = None
        final_text_deadline = None
        turn_started = False
        deadline = time.monotonic() + self.timeout_seconds

        def send(message: Dict[str, Any]) -> None:
            proc.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            proc.stdin.flush()

        def exit_detail() -> str:
            if proc.poll() is None:
                return ""
            tail = proc.stderr.read()[-1000:].strip()
            return ("; stderr: " + tail) if tail else ""

        try:
            send({"method": "initialize", "id": sequence, "params": {"clientInfo": {
                "name": "resource_review_bridge", "title": "Resource Review Bridge", "version": "0.1.0"
            }}})
            sequence += 1
            while time.monotonic() < deadline:
                if not selector.select(timeout=1):
                    if final_text is not None and final_text_deadline is not None and time.monotonic() >= final_text_deadline:
                        return json.loads(final_text)
                    if proc.poll() is not None:
                        raise AppServerError("app-server exited before completion" + exit_detail())
                    continue
                line = proc.stdout.readline()
                if not line:
                    raise AppServerError("app-server output closed before completion" + exit_detail())
                message = json.loads(line)
                if message.get("error"):
                    raise AppServerError(message["error"].get("message", "app-server protocol error"))
                if message.get("id") == 0 and thread_id is None:
                    send({"method": "initialized", "params": {}})
                    send({"method": "thread/start", "id": sequence, "params": {
                        "ephemeral": True,
                        "sandbox": "read-only",
                    }})
                    sequence += 1
                    continue
                if message.get("id") == 1 and thread_id is None:
                    thread_id = message.get("result", {}).get("thread", {}).get("id")
                    if not thread_id:
                        raise AppServerError("thread/start returned no thread ID")
                    inputs = [{"type": "text", "text": prompt}]
                    for image_path in local_images or []:
                        inputs.append({"type": "localImage", "path": image_path})
                    send({"method": "turn/start", "id": sequence, "params": {
                        "threadId": thread_id,
                        "input": inputs,
                        "cwd": cwd,
                        "sandboxPolicy": {"type": "readOnly"},
                        "approvalPolicy": "never",
                        "outputSchema": output_schema,
                    }})
                    sequence += 1
                    turn_started = True
                    continue
                if message.get("id") is not None and message.get("method"):
                    send({"id": message["id"], "error": {
                        "code": -32000,
                        "message": "Resource Review Bridge does not grant interactive or mutation requests.",
                    }})
                    continue
                if message.get("method") == "item/completed":
                    item = message.get("params", {}).get("item", {})
                    if item.get("type") == "agentMessage":
                        final_text = item.get("text")
                        # Some Codex app-server builds emit the completed agent
                        # item but omit turn/completed. Give the terminal event a
                        # short grace period, then accept the schema-bound item.
                        final_text_deadline = time.monotonic() + 2
                if message.get("method") == "turn/completed":
                    status = message.get("params", {}).get("turn", {}).get("status")
                    if status != "completed":
                        raise AppServerError("Codex turn ended with status %s" % (status or "unknown"))
                    if not final_text:
                        raise AppServerError("Codex completed without an Evidence Packet")
                    return json.loads(final_text)
            if final_text is not None:
                return json.loads(final_text)
            if turn_started:
                raise AppServerError("Codex review timed out")
            raise AppServerError("app-server initialization timed out")
        except (OSError, json.JSONDecodeError) as exc:
            raise AppServerError(str(exc)) from exc
        finally:
            selector.close()
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
