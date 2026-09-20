"""Isolated Playwright acquisition using an explicit, local, or Codex runtime."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _bundled_roots():
    return (
        Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies",
        Path.home() / ".codex/runtimes/codex-primary-runtime/dependencies",
    )


def _first_file(candidates):
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return None


def _first_directory(candidates):
    for candidate in candidates:
        if candidate and Path(candidate).is_dir():
            return str(candidate)
    return None


class BrowserAcquisitionError(RuntimeError):
    pass


class BrowserAcquirer:
    def __init__(self, timeout_seconds: int = 75):
        self.timeout_seconds = timeout_seconds
        self.node = os.environ.get("RESOURCE_REVIEW_NODE") or _first_file((
            shutil.which("node"),
            *(root / "node/bin/node" for root in _bundled_roots()),
        )) or "node"
        self.playwright = os.environ.get("RESOURCE_REVIEW_PLAYWRIGHT_PATH") or _first_directory((
            PROJECT_ROOT / "node_modules/playwright",
            *(root / "node/node_modules/playwright" for root in _bundled_roots()),
        )) or str(PROJECT_ROOT / "node_modules/playwright")
        self.script = Path(__file__).with_name("browser_acquire.mjs")

    def acquire(self, url: str, output_directory: str) -> Dict[str, Any]:
        if not Path(self.node).is_file():
            raise BrowserAcquisitionError("Node.js runtime is unavailable")
        if not Path(self.playwright).is_dir():
            raise BrowserAcquisitionError("Playwright runtime is unavailable")
        environment = os.environ.copy()
        environment["RESOURCE_REVIEW_PLAYWRIGHT_PATH"] = self.playwright
        try:
            completed = subprocess.run(
                [self.node, str(self.script), url, output_directory],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.timeout_seconds,
                env=environment,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BrowserAcquisitionError("browser acquisition did not complete") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip()[-1000:]
            raise BrowserAcquisitionError("browser acquisition failed%s" % ((": " + detail) if detail else ""))
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise BrowserAcquisitionError("browser acquisition returned invalid JSON") from exc
