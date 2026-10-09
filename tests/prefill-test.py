#!/usr/bin/env python3
"""Prefill-progress test for the nvtop LLM meter.

A stub router reports a loaded model whose slot is mid-prefill, so the PREF bar
can be checked without a real request. Three phases:

  A. prefill in flight, counter advancing -> bar, percentage, tokens/s
  B. is_processing true but processed == total (decoding) -> must fall back to
     the context bar rather than freeze at 100%
  C. a mostly-cached prompt -> the cache figure must be on screen, not hidden
"""
import http.server, json, os, re, subprocess, threading, urllib.parse

PORT = 56002
NVTOP = os.environ.get("NVTOP_BIN", "/home/user/tmp/nvtop/build-nv/src/nvtop")
RAW = "/home/user/tmp/ui-prefill.raw"
NAME = "Qwen3.5-35B-A3B-abliterated-128k"

# Real values from the live router mid-prefill on this box.
STATE = {"processed": 41408, "total": 43456, "cached": 0, "processing": True, "advance": 0,
         "spaced": False}

# Requests actually served, per phase: the point of the adaptive cadence is that
# this drops when nothing is happening.
REQS = {"models": 0, "slots": 0, "metrics": 0}
COUNTS = {}


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ct):
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        if p.path == "/v1/models":
            REQS["models"] += 1
            body = {"data": [{"id": NAME, "object": "model",
                    "status": {"value": "loaded", "preset": "",
                               "args": ["/usr/bin/llama-server", "--ctx-size", "131072",
                                        "--parallel", "1"]},
                    "meta": {"n_ctx": 131072, "n_ctx_train": 131072,
                             "size": 17486174848, "ftype": "IQ4_XS"}}], "object": "list"}
            self._send(json.dumps(body, separators=(", ", ": ") if STATE["spaced"] else (",", ":")).encode(),
                       "application/json")
        elif p.path == "/slots":
            REQS["slots"] += 1
            # Key order matters: is_processing precedes the counters, as on the
            # real router, and the poller parses forward from that match.
            # The real router advances a whole logical batch at a time, so a
            # coarse counter is modelled: step only every Nth request.
            STATE["req"] = STATE.get("req", 0) + 1
            slot = {"id": 0, "n_ctx": 131072, "speculative": False,
                    "is_processing": STATE["processing"], "id_task": 7,
                    "n_prompt_tokens": STATE["total"],
                    "n_prompt_tokens_processed": STATE["processed"],
                    "n_prompt_tokens_cache": STATE["cached"]}
            self._send(json.dumps([slot], separators=(", ", ": ") if STATE["spaced"] else (",", ":")).encode(),
                       "application/json")
            if STATE["req"] % STATE.get("advance_every", 1) == 0:
                STATE["processed"] += STATE["advance"]
        elif p.path == "/metrics":
            REQS["metrics"] += 1
            self._send((f'llamacpp:kv_cache_usage_ratio{{model="{NAME}"}} 0.75\n'
                        f'llamacpp:kv_cache_tokens{{model="{NAME}"}} 98304\n'
                        f'llamacpp:kv_cache_tokens_total{{model="{NAME}"}} 131072\n').encode(),
                       "text/plain")
        else:
            self.send_error(404)


def run(name, expect):
    env = dict(os.environ)
    env["NVTOP_LLM_SERVERS"] = f"stub=127.0.0.1:{PORT}=Radeon"
    env["TERM"] = "xterm-256color"
    env.pop("LINES", None)
    env.pop("COLUMNS", None)
    REQS.update(models=0, slots=0, metrics=0)
    subprocess.run(["timeout", "3", "script", "-qc",
                    f"stty rows 50 cols 200; {NVTOP} -d 5", RAW],
                   env=env, capture_output=True, timeout=30)
    raw = open(RAW, "rb").read().decode("utf-8", "replace")
    txt = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", raw)
    txt = re.sub(r"\x1b[()][A-Z0-9]", "", txt).replace("\r", "\n")
    print(f"--- {name} ---")
    for line in txt.split("\n"):
        if any(k in line for k in ("PREF", "CTX", "LOAD", "LLM")):
            print("   ", line.rstrip()[:150])
    COUNTS[name] = dict(REQS)
    print(f"    requests served in 3 s: {REQS}")
    pcts = [int(v) for v in re.findall(r"PREF\[[^\]]*\]\s+(\d+)%", txt)]
    return {label: cond(txt, pcts) for label, cond in expect.items()}


def main():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    STATE.update(processed=41408, total=43456, cached=0, processing=True, advance=300)
    a = run("A: prefill in flight", {
        "PREF bar drawn": lambda t, p: "PREF[" in t,
        "percentage from processed/total": lambda t, p: any(90 <= v <= 99 for v in p),
        "rate shown": lambda t, p: "tok/s" in t,
        "context bar not used at the same time": lambda t, p: not re.search(r"CTX\s*\[", t),
    })

    STATE.update(processed=43456, processing=True, advance=0)
    b = run("B: prompt done, decoding", {
        "no PREF bar": lambda t, p: "PREF[" not in t,
        "falls back to the context bar": lambda t, p: bool(re.search(r"CTX\s*\[", t)),
    })

    STATE.update(processed=20000, total=43456, cached=20000, processing=True, advance=0, spaced=True)
    c = run("C: mostly cached prompt", {
        "PREF bar drawn": lambda t, p: "PREF[" in t,
        "cache figure visible": lambda t, p: "cached" in t,
    })

    STATE.update(processed=0, total=0, cached=0, processing=False, advance=0, spaced=False)
    d = run("D: loaded but idle", {
        "no PREF bar when nothing is processing": lambda t, p: "PREF[" not in t,
    })

    # The easing of the bar between coarse counter steps is NOT tested here: this
    # harness scrapes the pty stream, and ncurses writes only the characters that
    # changed, so successive frames of one line cannot be recovered -- the
    # fragments join into a line that never existed on screen. It is unit-tested
    # instead, in tests/ease-test.c, against the pure step function.

    srv.shutdown()
    print("=" * 70)
    print("checks")
    print("=" * 70)
    a["cadence fast while a prompt is read"] = COUNTS["A: prefill in flight"]["models"] >= 5
    d["cadence slow while idle"] = COUNTS["D: loaded but idle"]["models"] <= 3
    print("  polls/3s: " + "  ".join(f"{k.split(':')[0]}={v['models']}" for k, v in COUNTS.items()))
    bad = 0
    for phase, res in (("A", a), ("B", b), ("C", c), ("D", d)):
        for k, v in res.items():
            print(f"  [{'PASS' if v else 'FAIL'}] {phase}: {k}")
            bad += 0 if v else 1
    print()
    print("ALL PASS" if not bad else f"{bad} FAILURE(S)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
