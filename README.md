# nvtop-llama

LLM model meters for [nvtop](https://github.com/Syllo/nvtop): what model each
llama.cpp router has loaded, how big it is, and how its context is doing —
shown under the GPU that serves it.

With the GT 1030 running two models at once:

```
Device 2 [NVIDIA GeForce GT 1030] ...
GPU 300MHz  MEM 6001MHz  TEMP 42dC  FAN 30%  POW 28 / 30 W
GPU[||||      ] MEM[||||||    ]
LLM llamb  granite-docling-258M-Q8_0  ctx 8.2k  275M  kv 75%
    llamb  Qwen3-Embedding-0.6B-Q8_0  ctx 4.1k  633M  kv 25%
```

With a single loaded model there is room to spare, so the second line gives the
full picture, or a context-load bar when live metrics are available:

```
LLM llama  Qwen3.5-35B-A3B-UD-IQ4_XS-240k
ctx 246k  train 256k  17.5G  34.7B  IQ4_XS  kv q8_0/q8_0
```

A GPU with no router mapped to it gets **no** meter band at all, and reserves no rows: meter rows are counted per header stack, and a stack is only as tall as its tallest member. Every GPU section is therefore separated by at most one blank row.

## What it reads

Per configured router:

| Endpoint | Used for |
| --- | --- |
| `GET /v1/models` | `id`, `status.value`, `status.args`, `meta{}` |
| `GET /metrics?model=<id>` | Prometheus `kv_cache_usage_ratio` (live context load) |

`meta{}` supplies `n_ctx`, `n_ctx_train`, `n_params`, `size` and `ftype`;
`status.args` supplies `--ctx-size`, `--parallel` and `--cache-type-k/v`.

**Every** model with `status.value == "loaded"` is listed, not just the first —
a router with `max_instances > 1` (llamb here is `2`) can serve several at once.

Live context load is best-effort. If the router cannot serve `/metrics`, the
meter falls back to the static facts line; it never invents a number. Repeated
refusals are backed off so the router log is not spammed.

## Install on Gentoo

User patches go in `/etc/portage/patches/<category>/<package>-<version>/` and are
applied by `eapply_user`, which `cmake.eclass` already calls:

```sh
sudo install -d          /etc/portage/patches/sys-process/nvtop-3.3.2
sudo install -m 644 patches/nvtop-3.3.2-llama-meter.patch \
                         /etc/portage/patches/sys-process/nvtop-3.3.2/llama-meter.patch
sudo emerge -1 --usepkg=n --getbinpkg=n nvtop
```

**`--usepkg=n --getbinpkg=n` is not optional if you run a binhost.** A binary
package is built elsewhere and never runs `src_prepare`, so `eapply_user` never
runs and this patch is skipped — with a normal-looking merge, exit 0, and no
warning anywhere. Plain `emerge -1 nvtop` will silently hand you an unpatched
binary. The tell is in the log: `* Applying user patches from
/etc/portage/patches ...` then `* Applying llama-meter.patch ...`. If those lines
are absent, the patch did not go in.

Patches are version specific on purpose: `interface.c` differs by hundreds of
lines between releases, so a single generic patch would fail to apply and break
the build. Install only the one matching the version you emerge.

`emerge` needs the USE flags for the backends you monitor, or the cards will not
be enumerated at all. With `VIDEO_CARDS` covering the cards, the ebuild passes
`-DNVIDIA_SUPPORT`, `-DAMDGPU_SUPPORT` and `-DINTEL_SUPPORT` accordingly.

Requires `net-misc/curl` (the poller links against libcurl).

## Version support

| nvtop | patch applies | compiles | meter renders |
| --- | --- | --- | --- |
| 3.1.0 | yes (via `ebuild prepare`) | yes | **not verified** — pristine 3.1.0 draws nothing in the test environment either |
| 3.2.0 | yes | yes | yes |
| 3.3.2 | yes | yes (via `ebuild compile`) | yes |
| 9999 | yes | yes | yes — but see below |

The `9999` patch was generated against master `71094a9f7dfb` (2026-10-04).
Because 9999 tracks a moving branch, that patch will go stale and, once it no
longer applies, `emerge nvtop-9999` will **fail** rather than silently build
without the meters. Delete it if you are not tracking master.

## Configuration

Routers default to `llama` on `127.0.0.1:55555` (GPU hint `7900`) and `llamb` on
`127.0.0.1:55556` (GPU hint `1030`). The hint is a case-insensitive substring
matched against the GPU device name to decide which GPU block the meter belongs
under. Override without rebuilding:

```sh
NVTOP_LLM_SERVERS="llama=127.0.0.1:55555=7900;llamb=127.0.0.1:55556=1030" nvtop
```

Format is `name=host:port=gpu_hint`, entries separated by `;`.

## Layout

The meter is a real window on rows the layout reserves for it, never a
subwindow — every nvtop info line is exactly one row tall, so a taller subwindow
spills over the plots. `compute_sizes_from_layout()` takes a per-device row
count and charges it to the header *stack*, so a GPU with no router reserves
nothing and cannot leave a blank band behind. On each resize nvtop tries the
tallest meter that keeps every chart and leaves a usable plot area, falling back
a row at a time and finally to no meter at all, so the graphs are never lost to
it.

## Regenerating the patches

```sh
python3 tools/port.py <pristine-nvtop-tree>
```

`port.py` detects the tree's shape (3.1.0, 3.2.0/3.3.2, or post-3.3.2 master),
copies the two new files in and applies every edit by asserted anchor, so an
unrecognised tree fails loudly instead of producing broken code. Then diff the
result against the pristine tree to make the patch.

## Tests

- `tests/stub-test.py` — serves a canned router (two models running, one
  unloaded, plus a Prometheus `/metrics`) and checks both the parser and the
  real UI rendering in a pty. Set `NVTOP_BIN` to the binary under test.
- `tests/layout-check.c` — calls the real `compute_sizes_from_layout()` and
  asserts the meter never overlaps the plots, never runs off-screen, and never
  costs a chart. Build with `-O2` (the inline helpers need optimisation).
- `tests/poller-test.c` — prints exactly what the meter will show, against the
  live routers.
- `tools/ansi-screen.py` — reconstructs a screen from a `script(1)`
  typescript, for inspecting what the TUI actually drew.

## Limitations

- Routers unload idle models, so `idle, none loaded` plus a preset count is a
  normal and truthful state, not a bug.
- The context bar appears only when the router serves `/metrics?model=`.
- A model is shown per row; if more are running than there are rows, the last
  row reports `+N more` rather than dropping them silently.

## Licence

GPL-3+ — these are patches against GPL-3+ nvtop; see `COPYING`.
