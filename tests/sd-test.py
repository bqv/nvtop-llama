#!/usr/bin/env python3
"""SD meter test: an A1111-shaped stub sd-server, plus a router on the same card.

Three phases, because the band is shared and the ordering is the point:
  1. SD sampling, router empty   -> SD takes the band
  2. SD idle, router empty       -> SD STILL takes it (an empty router is the
                                    normal state while a diffusion model is resident)
  3. SD idle, router loaded      -> the router takes both rows

The note is one-way by design: it appears on the *idle* line, where there is
room and where the other server is the resident one. While the router holds a
loaded model it owns both rows, and an idle diffusion checkpoint is not repeated
there.
"""
import http.server, json, os, re, subprocess, threading, urllib.parse

PORT = 56003
NVTOP = os.environ.get("NVTOP_BIN", "/home/user/tmp/nvtop/build-nv/src/nvtop")
RAW = "/home/user/tmp/ui-sd.raw"
FAKE = os.environ.get("FAKE_DIR", "/home/user/tmp/fakeload")
DIFF = "/home/user/var/model/diff"
LLM = "Qwen3.5-35B-A3B-abliterated-128k"
STATE = {"progress": 0.42, "eta": 180.0, "step": 8, "steps": 20, "jobs": 1,
         "llm_loaded": False}


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        if p.path == "/sdapi/v1/options":
            self._send({"samples_format": "png", "sd_model_checkpoint": "Qwen_Image-Q4_K_M"})
        elif p.path == "/sdapi/v1/progress":
            self._send({"current_image": None, "eta_relative": STATE["eta"],
                        "progress": STATE["progress"],
                        "state": {"interrupted": False, "job": "txt2img",
                                  "job_count": STATE["jobs"], "job_no": 0,
                                  "job_timestamp": "0", "sampling_step": STATE["step"],
                                  "sampling_steps": STATE["steps"], "skipped": False},
                        "textinfo": None})
        elif p.path == "/v1/models":
            m = {"id": LLM, "object": "model",
                 "status": {"value": "loaded" if STATE["llm_loaded"] else "unloaded",
                            "args": ["/usr/bin/llama-server", "--ctx-size", "131072",
                                     "--parallel", "1"], "preset": ""}}
            if STATE["llm_loaded"]:
                m["meta"] = {"n_ctx": 131072, "n_ctx_train": 262144,
                             "size": 17486174848, "ftype": "IQ4_XS"}
            self._send({"data": [m], "object": "list"})
        else:
            self.send_error(404)


def run(name, expect):
    env = dict(os.environ)
    env["NVTOP_SD_SERVERS"] = f"sd=127.0.0.1:{PORT}=Radeon"
    env["NVTOP_LLM_SERVERS"] = f"llama=127.0.0.1:{PORT}=7900"  # same card
    env["TERM"] = "xterm-256color"
    env.pop("LINES", None)
    env.pop("COLUMNS", None)
    subprocess.run(["timeout", "3", "script", "-qc",
                    f"stty rows 50 cols 200; {NVTOP} -d 5", RAW],
                   env=env, capture_output=True, timeout=30)
    raw = open(RAW, "rb").read().decode("utf-8", "replace")
    txt = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", raw)
    txt = re.sub(r"\x1b[()][A-Z0-9]", "", txt).replace("\r", "\n")
    print(f"--- {name} ---")
    for line in txt.split("\n"):
        if any(k in line for k in ("SD", "DIFF", "LLM", "router")):
            print("   ", line.rstrip()[:140])
    return {k: c(txt) for k, c in expect.items()}


def main():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    # A fake sd-server reading the real weights, so the /proc load scan has
    # something true to measure. FAKELOAD_READY_BYTES is the pipe handshake.
    STATE.update(progress=0.0, step=0, steps=0, jobs=1, eta=0.0)
    files = ["Qwen_Image-Q4_K_M.gguf", "Qwen_Image-VAE.safetensors",
             "Qwen2.5-VL-7B-Instruct-abliterated.Q5_K_M.gguf"]
    args = [os.path.join(FAKE, "sd-server")]
    for flag, f in zip(("--diffusion-model", "--vae", "--llm"), files):
        args += [flag, os.path.join(DIFF, f)]
    kid = subprocess.Popen(args, env={**os.environ, "FAKELOAD_READY_BYTES": str(1 << 30)},
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    kid.stdout.readline()
    try:
        l = run("0b. loading the weights", {
            "LOAD bar drawn": lambda t: "LOAD[" in t,
            "percentage from read_bytes over the model files":
                lambda t: any(1 <= int(v) <= 99 for v in re.findall(r"LOAD\[[^\]]*\]\s+(\d+)%", t)),
            "says loading": lambda t: "loading" in t,
            "no DIFF bar while loading": lambda t: "DIFF[" not in t,
        })
    finally:
        kid.kill()
        kid.wait()

    # Now the same state with no load signal available at all.
    STATE.update(progress=0.0, step=0, steps=0, jobs=1, eta=0.0)
    z = run("0. job accepted, no load signal available", {
        "no DIFF bar while loading": lambda t: "DIFF[" not in t,
        "honest about having no progress": lambda t: "no progress reported" in t,
        "SD still owns the band": lambda t: "SD sd" in t,
    })

    STATE.update(progress=0.42, step=8, steps=20, jobs=1, eta=180.0)
    a = run("1. SD sampling, router empty", {
        "diffusion takes the band": lambda t: "DIFF[" in t,
        "checkpoint from sd_model_checkpoint": lambda t: "Qwen_Image-Q4_K_M" in t,
        "percentage from progress": lambda t: bool(re.search(r"DIFF\[[^\]]*\]\s+42%", t)),
        "step counter": lambda t: "step 8/20" in t,
        "eta from eta_relative": lambda t: "eta 180s" in t,
        "no router note while sampling": lambda t: "| LLM" not in t,
    })

    STATE.update(progress=0.0, step=0, jobs=0, eta=0.0)
    b = run("2. SD idle, router empty -> SD keeps the top row", {
        "no DIFF bar": lambda t: "DIFF[" not in t,
        "SD still owns the band": lambda t: "SD sd" in t,
        "idle line": lambda t: "idle, no job in flight" in t,
        "router mentioned on its line": lambda t: "| LLM llama idle" in t,
    })

    STATE["llm_loaded"] = True
    c = run("3. SD idle, router loaded -> router owns the band, SD still named", {
        "router owns the band": lambda t: f"LLM llama  {LLM}" in t,
        "SD named on the router's line": lambda t: "| SD Qwen_Image-Q4_K_M idle" in t,
        "no DIFF bar": lambda t: "DIFF[" not in t,
    })

    srv.shutdown()
    bad = 0
    for phase, res in (("0", z), ("0b", l), ("1", a), ("2", b), ("3", c)):
        for k, v in res.items():
            print(f"  [{'PASS' if v else 'FAIL'}] {phase}: {k}")
            bad += 0 if v else 1
    print()
    print("ALL PASS" if not bad else f"{bad} FAILURE(S)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
