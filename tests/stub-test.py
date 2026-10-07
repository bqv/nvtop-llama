#!/usr/bin/env python3
"""
Deterministic test for the nvtop LLM meter.

Serves a canned llama.cpp-router /v1/models with TWO models running at once
(plus one unloaded), and a /metrics endpoint with per-model KV load, then:

  1. runs the poller test against it
  2. runs the real nvtop UI in a pty against it, with the stub's gpu_hint set
     to match the only GPU visible in this sandbox, and captures the screen

The stub reproduces the real schema observed on the live routers.
"""

import http.server
import json
import os
import re
import subprocess
import sys
import threading
import urllib.parse

PORT = 55999
NVTOP = os.environ.get("NVTOP_BIN", "/home/user/tmp/nvtop/build-nv/src/nvtop")
POLLER = os.environ.get("POLLER_BIN", "/home/user/tmp/poller-test")
RAW = "/home/user/tmp/ui-models.raw"

MODELS = {
    "data": [
        {
            "id": "granite-docling-258M-Q8_0",
            "object": "model",
            "status": {
                "value": "loaded",
                "args": ["/usr/bin/llama-server", "--ctx-size", "8192", "--parallel", "2",
                         "--cache-type-k", "q8_0", "--cache-type-v", "q8_0"],
                "preset": "",
            },
            "meta": {"n_ctx": 8192, "n_ctx_train": 8192, "n_params": 258000000,
                     "size": 275000000, "ftype": "Q8_0"},
        },
        {
            "id": "Qwen3-Embedding-0.6B-Q8_0",
            "object": "model",
            "status": {"value": "loaded", "args": ["/usr/bin/llama-server", "--ctx-size", "4096"], "preset": ""},
            "meta": {"n_ctx": 4096, "n_ctx_train": 32768, "n_params": 595776512,
                     "size": 633205056, "ftype": "Q8_0"},
        },
        {
            "id": "Pleias-RAG-1B-Q4_K_M",
            "object": "model",
            "status": {"value": "unloaded", "args": [], "preset": ""},
        },
    ],
    "object": "list",
}

# Distinct KV load per model, so a mix-up would be visible.
KV = {"granite-docling-258M-Q8_0": (0.75, 6144, 8192), "Qwen3-Embedding-0.6B-Q8_0": (0.25, 1024, 4096)}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/v1/models":
            body = json.dumps(MODELS).encode()
            ctype = "application/json"
        elif parsed.path == "/metrics":
            q = urllib.parse.parse_qs(parsed.query)
            name = (q.get("model") or [""])[0]
            if name in KV:
                ratio, tokens, total = KV[name]
                body = (
                    "# HELP llamacpp:kv_cache_usage_ratio KV cache usage\n"
                    "# TYPE llamacpp:kv_cache_usage_ratio gauge\n"
                    f'llamacpp:kv_cache_usage_ratio{{model="{name}"}} {ratio}\n'
                    f'llamacpp:kv_cache_tokens{{model="{name}"}} {tokens}\n'
                    f'llamacpp:kv_cache_tokens_total{{model="{name}"}} {total}\n'
                ).encode()
            else:
                body = b""
            ctype = "text/plain"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    env = dict(os.environ)
    env["NVTOP_LLM_SERVERS"] = f"stub=127.0.0.1:{PORT}=Xeon"
    # ncurses needs a capable TERM or it degrades to line-mode and draws nothing.
    env["TERM"] = "xterm-256color"
    env.pop("LINES", None)
    env.pop("COLUMNS", None)

    print("=" * 70)
    print("1) poller against the stub (2 models running, 1 unloaded)")
    print("=" * 70)
    r = subprocess.run([POLLER], env=env, capture_output=True, text=True, timeout=30)
    print(r.stdout, end="")
    if r.returncode != 0:
        print("poller exit", r.returncode, r.stderr)

    print("=" * 70)
    print("2) real nvtop UI in a pty against the stub")
    print("=" * 70)
    subprocess.run(
        ["timeout", "3", "script", "-qc", f"stty rows 50 cols 200; {NVTOP} -d 5", RAW],
        env=env, capture_output=True, timeout=30,
    )
    raw = open(RAW, "rb").read().decode("utf-8", "replace")
    txt = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", raw)
    txt = re.sub(r"\x1b[()][A-Z0-9]", "", txt).replace("\r", "\n")

    print("--- lines mentioning the meter or the models ---")
    for line in txt.split("\n"):
        if any(k in line for k in ("LLM", "granite", "Qwen3-Embedding", "CTX")):
            print("   ", line.rstrip()[:180])

    srv.shutdown()

    print("=" * 70)
    print("checks")
    print("=" * 70)
    checks = [
        ("both running models collected", "running=2" in r.stdout),
        ("first model ctx 8192 parsed", "ctx 8192" in r.stdout),
        ("second model ctx 4096 parsed", "ctx 4096" in r.stdout),
        ("parallel=2 from args", "slots 2" in r.stdout),
        ("kv cache types from args", "kv q8_0/q8_0" in r.stdout),
        ("unloaded model excluded", "Pleias" not in r.stdout),
        ("live ctx 75% parsed for model 1", "live ctx: 75%" in r.stdout),
        ("live ctx 25% parsed for model 2", "live ctx: 25%" in r.stdout),
        ("UI shows first model name", "granite-docling-258M-Q8_0" in txt),
        ("UI shows second model name", "Qwen3-Embedding-0.6B-Q8_0" in txt),
        ("UI shows the LLM label", "LLM" in txt),
    ]
    bad = 0
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        bad += 0 if ok else 1
    print()
    print("ALL PASS" if not bad else f"{bad} FAILURE(S)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
