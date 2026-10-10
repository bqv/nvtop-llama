#!/usr/bin/env python3
"""Loading-state test for the nvtop LLM meter.

A stub router claims a model is loading while a fake llama-server child does
real O_DIRECT reads of a GGUF, so the LOAD bar can be checked without loading a
real model. Two phases: a moving counter (percentage + rate) and a static one
(the mmap trap, which must be called stalled, not drawn as a frozen bar)."""
import glob, http.server, json, os, re, subprocess, sys, threading, urllib.parse

PORT = 56001
NVTOP = os.environ.get("NVTOP_BIN", "/home/user/tmp/nvtop/build-nv/src/nvtop")
FAKE = "/home/user/tmp/fakeload"
RAW = "/home/user/tmp/ui-load.raw"
MODELS_DIR = "/home/user/var/model/llm"

def biggest(lo, hi):
    """Largest GGUF in [lo, hi]; falls back to any GGUF, and None only if the
    model dir is empty (the fixture must not depend on one particular rung)."""
    files = sorted(glob.glob(os.path.join(MODELS_DIR, "*.gguf")), key=os.path.getsize, reverse=True)
    for f in files:
        if lo <= os.path.getsize(f) <= hi:
            return f
    return files[0] if files else None

def run_phase(name, model_arg, cwd, pause, expect):
    body = {"data": [{"id": os.path.basename(model_arg), "object": "model",
            "status": {"value": "loading", "preset": "",
                       "args": ["/usr/bin/llama-server", "--model", "./" + model_arg,
                                "--ctx-size", "262144", "--parallel", "1",
                                "--cache-type-k", "q8_0", "--cache-type-v", "q8_0"]}}],
            "object": "list"}  # no meta{}: the real router omits it until loaded
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            p = urllib.parse.urlparse(self.path)
            if p.path == "/v1/models":
                b = json.dumps(body).encode(); ct = "application/json"
            else:
                self.send_error(500); return
            self.send_response(200); self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(b))); self.end_headers()
            self.wfile.write(b)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    env = dict(os.environ)
    env["NVTOP_LLM_SERVERS"] = f"stub=127.0.0.1:{PORT}=Radeon"
    env["TERM"] = "xterm-256color"
    env["NVTOP_SD_SERVERS"] = "none=127.0.0.1:9=none"  # keep the real SD out of this suite
    if pause: env["FAKELOAD_PAUSE"] = "1"
    env.pop("LINES", None); env.pop("COLUMNS", None)

    kid = subprocess.Popen([os.path.join(FAKE, "llama-server"), "--model", "./" + model_arg,
                            "--ctx-size", "262144"], cwd=cwd, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    # Wait for the *event*: the child reports it has read a known amount, so a
    # captured frame cannot show 0% just because nothing had been read yet.
    kid.stdout.readline()
    try:
        subprocess.run(["timeout", "3", "script", "-qc", f"stty rows 50 cols 200; {NVTOP} -d 5", RAW],
                       env=env, capture_output=True, timeout=30)
    finally:
        kid.kill(); kid.wait()
    srv.shutdown()
    raw = open(RAW, "rb").read().decode("utf-8", "replace")
    txt = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", raw)
    txt = re.sub(r"\x1b[()][A-Z0-9]", "", txt).replace("\r", "\n")
    print(f"--- {name}: {txt.count('Device')} Device lines drawn ---")
    print(f"--- {name}: lines mentioning LOAD/LLM ---")
    for line in txt.split("\n"):
        if "LOAD" in line or "LLM" in line:
            print("   ", line.rstrip()[:150])
    pcts = [int(m) for m in re.findall(r"LOAD\[[^\]]*\]\s+(\d+)%", txt)]
    ok = {}
    for label, cond in expect.items():
        ok[label] = cond(txt, pcts)
    return ok

def main():
    big = biggest(3 << 30, 6 << 30)
    if big is None:
        print("no GGUF under", MODELS_DIR, "-- cannot run the load phases")
        return 2
    print("phase 1 GGUF:", os.path.basename(big), f"{os.path.getsize(big)/(1<<30):.2f} GiB")
    small = os.path.join(FAKE, "small.bin")
    with open(small, "wb") as f: f.write(b"\0" * (2 << 20))

    a = run_phase("moving counter", os.path.basename(big), MODELS_DIR, False, {
        "LOAD bar drawn": lambda t, p: "LOAD[" in t,
        "a mid-load percentage (1..99)": lambda t, p: any(1 <= v <= 99 for v in p),
        # NOT asserted: "MB/s" only appears from the second sample on, and this
        # harness cannot see successive frames of one line (ncurses writes only
        # the characters that changed). Rate/ETA belong in a unit test.
        "GRT 1030 not blamed / no false 'model loaded'": lambda t, p: "model loaded" not in t,
    })
    # Must live on the same real filesystem as the models: read_bytes counts
    # block-layer reads, and it is 0 for a file on the scratch fs (which is why
    # this test could not use ~/tmp for the completed-read phase).
    aux = min(glob.glob("/home/user/var/model/aux/*.gguf"), key=os.path.getsize)
    print("phase 2 GGUF:", os.path.basename(aux), f"{os.path.getsize(aux)/1048576:.0f} MiB")
    b = run_phase("static counter (mmap trap)", os.path.basename(aux), os.path.dirname(aux), True, {
        "the completed read showing ~100%": lambda t, p: any(v >= 99 for v in p),
        "stalled verdict, not a frozen bar": lambda t, p: "no read progress" in t,
    })

    print("=" * 70); print("checks"); print("=" * 70)
    bad = 0
    for phase, res in (("moving", a), ("static", b)):
        for k, v in res.items():
            print(f"  [{'PASS' if v else 'FAIL'}] {phase}: {k}")
            bad += 0 if v else 1
    print(); print("ALL PASS" if not bad else f"{bad} FAILURE(S)")
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main())
