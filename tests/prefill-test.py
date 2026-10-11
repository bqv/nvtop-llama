#!/usr/bin/env python3
"""Prefill-progress test for the nvtop LLM meter.

A stub router reports a loaded model whose slot is mid-prefill, so the PREF bar
can be checked without a real request. Six phases:

  A. prefill in flight, counter advancing -> bar, percentage, tokens/s. The prompt
     length is in no endpoint: n_prompt_tokens is processed + cache + one batch, so
     dividing by it measures nothing (it slides to 100% on its own). The percentage
     is measured against the context prefix carried into the request, which the
     first poll of it still reports.
  B. is_processing true but processed == total (decoding, and the router reports
     no next_token) -> must fall back to the context bar, not freeze at 100%
  C. a mostly-cached prompt -> the cache figure must be on screen, not hidden
  D. loaded but idle -> no bar at all
  E. the live bug: a cache-heavy request that is ALREADY GENERATING. The router
     reports is_processing, a sequence length well past the prompt, a large cache
     figure and a near-zero processed count, while next_token.n_decoded climbs.
     `processed >= total` never fires in that state, so the prefill bar used to
     sit at 0% for the whole generation and hide the context bar with it.
  F. generating with no cache -> a decode bar of its own, filled against the
     generation allowance the router reports
"""
import http.server, json, os, re, subprocess, threading, urllib.parse

PORT = 56002
NVTOP = os.environ.get("NVTOP_BIN", "/home/user/tmp/patches-work/w332/build/src/nvtop")
RAW = "/home/user/tmp/ui-prefill.raw"
NAME = "Qwen3.5-35B-A3B-abliterated-128k"

# Real values from the live router mid-prefill on this box.
STATE = {"processed": 41408, "total": 43456, "cached": 0, "processing": True, "advance": 0,
         "spaced": False,
         # None = a build that reports no next_token at all; a number is
         # next_token.n_decoded, which is how the poller tells "reading a prompt"
         # from "generating". n_remain is the allowance still open to the task.
         "decoded": None, "remain": 62000}

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
            # next_token comes last on the real router. n_decoded in it is the
            # only reliable "generation has begun" signal: during decode
            # n_prompt_tokens is the whole sequence and keeps growing while
            # processed stays wherever the prompt left it.
            if STATE["decoded"] is not None:
                slot["next_token"] = [{"has_next_token": True, "n_decoded": STATE["decoded"],
                                       "n_remain": STATE["remain"]}]
            self._send(json.dumps([slot], separators=(", ", ": ") if STATE["spaced"] else (",", ":")).encode(),
                       "application/json")
            if STATE["req"] % STATE.get("advance_every", 1) == 0:
                STATE["processed"] += STATE["advance"]
                # A generating task advances n_decoded instead; the poller needs
                # two samples of it to have a rate to show.
                if STATE["decoded"] is not None:
                    STATE["decoded"] += STATE.get("advance_decoded", 0)
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
    env["NVTOP_SD_SERVERS"] = "none=127.0.0.1:9=none"  # keep the real SD out of this suite
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
        if any(k in line for k in ("PREF", "CTX", "LOAD", "LLM", "GEN")):
            print("   ", line.rstrip()[:150])
    COUNTS[name] = dict(REQS)
    print(f"    requests served in 3 s: {REQS}")
    pcts = [int(v) for v in re.findall(r"PREF\[[^\]]*\]\s+(\d+)%", txt)]
    return {label: cond(txt, pcts) for label, cond in expect.items()}


def main():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    # A prefill as the router really presents it. The prompt's own length is in no
    # endpoint, and n_prompt_tokens is processed + cache + one batch, so the only
    # denominator available is the context prefix carried into the request -- which
    # the first poll still shows: 43,456 here, against a counter starting at zero.
    STATE.update(processed=30000, total=43456, cached=0, processing=True, advance=500,
                 spaced=False, decoded=None, advance_decoded=0)
    a = run("A: prefill in flight", {
        # This harness scrapes the pty stream and ncurses writes only changed
        # cells, so a row that changes between frames cannot be reassembled: the
        # bracket can arrive as "]" on its own, and a frame drawn before the first
        # /slots poll leaves its CTX line in the stream. Assert loosely on purpose.
        "PREF bar drawn": lambda t, p: "PREF" in t,
        "a percentage is shown": lambda t, p: bool(re.search(r"PREF[^\n]{0,200}?(\d+)%", t)),
        "rate shown": lambda t, p: "tok/s" in t,
    })

    STATE.update(processed=43456, processing=True, advance=0, decoded=None)
    b = run("B: prompt done, decoding, no next_token reported", {
        "no PREF bar": lambda t, p: "PREF[" not in t,
        "falls back to the context bar": lambda t, p: bool(re.search(r"CTX\s*\[", t)),
    })

    STATE.update(processed=0, total=43456, cached=20000, processing=True, advance=0,
                 spaced=True, decoded=None, advance_decoded=0)
    c = run("C: mostly cached prompt", {
        "PREF bar drawn": lambda t, p: "PREF[" in t,
        "cache figure visible": lambda t, p: "cached" in t,
    })

    # The live bug, exactly as measured off task 555381: the prompt was 26,494
    # tokens, of which 23,636 were served from the router's prompt cache, so its
    # evaluated counter sat at 4 while n_prompt_tokens -- really the whole
    # sequence -- grew with every generated token. `processed >= total` could
    # never fire there, so the prefill bar stayed at 0% for the whole generation
    # and hid the context bar with it. next_token.n_decoded is what gives it away.
    STATE.update(processed=4, total=27025, cached=23636, processing=True, advance=0,
                 spaced=False, decoded=3385, remain=62000, advance_decoded=100)
    e = run("E: cache-heavy request already generating", {
        "no PREF bar while generating": lambda t, p: "PREF[" not in t,
        # "GEN" is three characters in the same four-column label field the other
        # bars use, so the bracket sits one column further along: "GEN [".
        "decode gets its own bar": lambda t, p: bool(re.search(r"GEN\s*\[", t)),
        "generation rate shown": lambda t, p: "tok/s" in t,
        "context fill kept in the tail": lambda t, p: "ctx" in t,
    })

    # A plain decode with no cache involved: the bar is the share of the
    # generation allowance the router reports, so a fresh response starts low.
    STATE.update(processed=43456, total=43456, cached=0, processing=True, advance=0,
                 decoded=1000, remain=10000, advance_decoded=100)
    f = run("F: generating, no cache", {
        "decode bar drawn": lambda t, p: bool(re.search(r"GEN\s*\[", t)),
        "allowance share shown": lambda t, p:
            any(5 <= int(v) <= 15 for v in re.findall(r"GEN\s*\[[^\]]*\]\s+(\d+)%", t)),
        "tokens generated shown": lambda t, p: " gen" in t,
    })

    STATE.update(processed=0, total=0, cached=0, processing=False, advance=0, spaced=False,
                 decoded=None, advance_decoded=0)
    d = run("D: loaded but idle", {
        "no PREF bar when nothing is processing": lambda t, p: "PREF[" not in t,
        "no decode bar either": lambda t, p: not re.search(r"GEN\s*\[", t),
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
    for phase, res in (("A", a), ("B", b), ("C", c), ("D", d), ("E", e), ("F", f)):
        for k, v in res.items():
            print(f"  [{'PASS' if v else 'FAIL'}] {phase}: {k}")
            bad += 0 if v else 1
    print()
    print("ALL PASS" if not bad else f"{bad} FAILURE(S)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
