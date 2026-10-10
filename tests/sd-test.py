#!/usr/bin/env python3
"""SD meter test: an A1111-shaped stub sd-server, and the real nvtop UI.

Phase 1 has a job sampling, so the diffusion server must take the GPU's band
away from the router mapped to the same card. Phase 2 is idle, where the router
gets the band back and the diffusion checkpoint is noted on its own line.
"""
import http.server, json, os, re, subprocess, threading, urllib.parse

PORT = 56003
NVTOP = os.environ.get("NVTOP_BIN", "/home/user/tmp/nvtop/build-nv/src/nvtop")
RAW = "/home/user/tmp/ui-sd.raw"
STATE = {"progress": 0.42, "eta": 180.0, "step": 8, "steps": 20, "jobs": 1}


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
            # Key order as the real server sends it (alphabetical).
            self._send({"current_image": None, "eta_relative": STATE["eta"],
                        "progress": STATE["progress"],
                        "state": {"interrupted": False, "job": "txt2img",
                                  "job_count": STATE["jobs"], "job_no": 0,
                                  "job_timestamp": "0", "sampling_step": STATE["step"],
                                  "sampling_steps": STATE["steps"], "skipped": False},
                        "textinfo": None})
        else:
            self.send_error(404)


def run(name, expect):
    env = dict(os.environ)
    env["NVTOP_SD_SERVERS"] = f"sd=127.0.0.1:{PORT}=Radeon"
    # A router mapped to the SAME card, and deliberately not answering, so the
    # contest for the shared band is what gets tested.
    env["NVTOP_LLM_SERVERS"] = "llama=127.0.0.1:9=7900"
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

    a = run("sampling", {
        "diffusion takes the band": lambda t: "DIFF[" in t,
        "checkpoint from sd_model_checkpoint": lambda t: "Qwen_Image-Q4_K_M" in t,
        "percentage from progress": lambda t: bool(re.search(r"DIFF\[[^\]]*\]\s+42%", t)),
        "step counter": lambda t: "step 8/20" in t,
        "eta from eta_relative": lambda t: "eta 180s" in t,
    })
    STATE.update(progress=0.0, step=0, jobs=0, eta=0.0)
    b = run("idle", {
        "no DIFF bar": lambda t: "DIFF[" not in t,
        "checkpoint still visible": lambda t: "Qwen_Image-Q4_K_M" in t,
    })

    srv.shutdown()
    bad = 0
    for phase, res in (("sampling", a), ("idle", b)):
        for k, v in res.items():
            print(f"  [{'PASS' if v else 'FAIL'}] {phase}: {k}")
            bad += 0 if v else 1
    print()
    print("ALL PASS" if not bad else f"{bad} FAILURE(S)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
