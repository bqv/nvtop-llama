# nvtop-llama

**A fork of [nvtop](https://github.com/Syllo/nvtop).** `master` is upstream's
history plus the changes below, so it builds as-is; the files in
[`patches/`](patches/) describe those same changes against a *pristine* upstream
tree, which is how they are applied on Gentoo. Upstream's own README is kept
beside this one as [`NVTOP-README.markdown`](NVTOP-README.markdown).

LLM model meters for nvtop: what model each llama.cpp router has loaded, how big
it is, and how its context is doing — shown under the GPU that serves it.

| what this fork adds | status |
| --- | --- |
| **LLM model meters** — a line per loaded model, under the GPU serving it | in this fork |
| **Clock chart clamped to 100%**, so a boosting GPU stops erasing the line | offered upstream: [Syllo/nvtop#529](https://github.com/Syllo/nvtop/pull/529) |

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
V=3.3.2
P=/etc/portage/patches/sys-process/nvtop-$V
sudo install -d "$P"
sudo install -m 644 patches/nvtop-$V-clock-percent-clamp.patch "$P/clock-percent-clamp.patch"
sudo install -m 644 patches/nvtop-$V-llama-meter.patch         "$P/llama-meter.patch"
sudo emerge -1 --usepkg=n --getbinpkg=n nvtop
```

**`--usepkg=n --getbinpkg=n` is not optional if you run a binhost.** A binary
package is built elsewhere and never runs `src_prepare`, so `eapply_user` never
runs and this patch is skipped — with a normal-looking merge, exit 0, and no
warning anywhere. Plain `emerge -1 nvtop` will silently hand you an unpatched
binary. The tell is in the log: `* Applying user patches from
/etc/portage/patches ...` then one line per patch, `* Applying
clock-percent-clamp.patch ...` then `* Applying llama-meter.patch ...`. If those
lines are absent, the patch did not go in.

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

## A fix to nvtop's clock chart

This is a bug in nvtop rather than in the meter, so it sits in its own patch file
and was offered upstream on its own, without the meter:
[Syllo/nvtop#529](https://github.com/Syllo/nvtop/pull/529).

`patches/nvtop-<version>-clock-percent-clamp.patch` — the clock chart plots a
percentage of the maximum the backend reports:

    data_val = gpu_clock_speed * 100 / gpu_clock_speed_max;

and on this 7900 XT those two disagree by a third: the SMU reports 2894-2911 MHz
under load while `max_engine_clk` — and `pp_dpm_sclk`, and everything else the
driver exposes — caps at 2175 MHz. The card boosts above every maximum it
advertises; `hwmon` has `freq1_input` but no `freq1_max`. Measured over 60 samples
at full load: 109-133%, over 100 in every one of them.

A percentage above 100 does not merely run off the top of the chart.
`nvtop_line_plot()` maps a value to a row with `rows - round(data/increment)`,
which is negative here, and stores it in an `unsigned`: 133% on a 20-row chart
becomes 4294967289. The sample then looks like an enormous increase, the corner
is drawn far outside the window (so nothing appears), and `mvwvline()` is asked
for a four-billion-cell vertical line, which ncurses clips into a full-height bar.
While the value stays above 100% every frame takes the "stayed level" branch and
draws at row 4294967289 -- no line at all. That is the disconnected clock graph:
the clock line vanishes whenever the card boosts, with stray bars where it
crosses the 100% boundary.

The temperature and power cases a few lines above already clamp exactly like
this; the clock cases were missed. Two lines per case, and on this card the chart
then shows the clock pinned at 100% while boosting -- on a 0-100 axis it cannot
show more. One file per version: the surrounding case labels differ (3.1.0 and
3.2.0 have no `eff. load` case after the memory clock), so a single file does not
apply strictly to all four.

Verified as an A/B: two builds of pristine 3.3.2 differing only in those four
lines, run simultaneously on 136x65 pty pairs, with each chart line's colour
counted inside the 7900's chart band by `tools/colour-screen.py`. Boosting at
89-133%, the clock line had 17 cells in the unpatched build and 110 in the patched
one. It reappears along the shared 100% row and overdraws the GPU% line drawn
there, so GPU% falls from 116 cells to 42, while mem% and temp move by only 4 and
11. Idle, with nothing above the maximum, the clock line is identical in both
builds -- 55 cells either way -- which is what the clamp is supposed to do.

A second nvtop bug found on the way -- `compute_sizes_from_layout()` initialising
the chart cursor once, outside the per-stack loop, so that every stack after the
first was laid out starting where the previous one ended, off the right-hand edge
-- was fixed here and has since been removed again at the owner's request. A stack
only holds several charts on a wide terminal, and the one this is used on is 65
columns wide, where the loop body runs once; the fix is still in the history if it
is ever wanted back (`git log --oneline --grep='chart stack after the first'`).

## Stable-diffusion status

`stable-diffusion.cpp`'s `sd-server` is not a router, but it speaks an
A1111-shaped API, so its meter uses the same two rows:

    SD sd  Qwen_Image-Q4_K_M
    DIFF[||||||||||||||        ] 42%  step 8/20  eta 180s

`GET /sdapi/v1/options` gives `sd_model_checkpoint`; `GET /sdapi/v1/progress`
gives `progress`, `eta_relative` and `state.sampling_step` / `sampling_steps`.
Servers are configured with `NVTOP_SD_SERVERS`, in the same
`name=host:port=gpu-hint` shape as `NVTOP_LLM_SERVERS`:

    NVTOP_SD_SERVERS="sd=127.0.0.1:55557=7900"

The details that matter:

 - `?skip_current_image=true` is always sent. While a job runs, `current_image`
   is a data URL of the image so far, and because the keys come out
   alphabetically it would sit in front of every field wanted and push them past
   the response buffer.
 - **The band is shared with the LLM meter, so the header never grows.** Which
   server takes it follows what is actually on the card: a diffusion server that
   is generating, then a router with a model loaded, then a reachable diffusion
   server, then the router. That middle pair matters -- while a diffusion model is
   resident the router is idle and unloaded, so diffusion keeps the top row even
   between jobs and the router is noted on its line (`| LLM llama idle`). The note
   is one-way, appearing on the idle line where there is room. A GPU with only one
   of the two configured still gets the rows, because the reservation asks for
   *either*.
 - **The load is trackable, the same way the llama load bar is.** The sd child
   names every file it must load on its own command line (`--diffusion-model`,
   `--vae`, `--llm`), so nvtop sums their sizes and watches `read_bytes` in
   `/proc/<pid>/io`:

       SD sd  Qwen_Image-Q4_K_M  loading
       LOAD[||||||||          ] 42%  7.3/17.5 GiB

   Measured at 97% of 17.48 GiB once loaded, the card reaching 12.35 GiB. This is
   the minutes-long phase the sdapi document cannot see at all -- it reports
   `progress 0` throughout, so drawing a bar from it would claim `DIFF 0%`.
 - **A job in flight is not the same as sampling**, so the phase is named in words
   on the name row: `loading` (weights still being read), `diffusing` (`progress`
   or `sampling_step` moving), `working` (job in flight, weights in, nothing
   reported -- the CPU text encoder sits here for ~80 s of a small job), `idle`.
 - Presence is reachability and nothing else: sd-server always names a checkpoint
   and reports progress 0 whether idle or unloaded (`/sdapi/v1/memory` is 404), so
   a server that does not answer is not shown at all and reserves no rows.

Tested by `tests/sd-test.py` against a stub: while sampling, diffusion takes the
band away from a router mapped to the same card; once idle the router gets it
back and the checkpoint stays visible.

## Cold-load progress

While a model is loading, the meter shows how far the load has got:

    LLM llama  loading  Qwen3.5-35B-A3B-UD-IQ4_XS-240k
    LOAD[||||||||         ]  38%  6.2/16.3 GiB  780 MB/s  eta 13s

This is not an API signal -- llama.cpp's router only reports `loading` or
`loaded`, never a number. nvtop reads `read_bytes` from `/proc/<pid>/io` for the
per-model `llama-server` child (the one carrying `--model`; the router carries
`--models-dir` instead) and divides it by the size of the GGUF that child was
told to load.

That is only a valid signal because `models.ini` sets `load-mode = dio`:
DirectIO sends every byte through the block layer, so `read_bytes` advances in
step with the load. Under the default mmap loading the counter stalls while pages
are served from page cache and the percentage would be a lie -- so a counter that
has stopped moving is reported as

    LOAD[]  no read progress (load-mode not dio?)

rather than as a frozen bar. Two related traps, both handled: `read_bytes` also
counts the process's other reads (shared libraries, the vocab, a second pass over
the header), so it can exceed the file size, and both the percentage and the
displayed size are clamped; and 100% means "the weights have been read", not "the
model is serving", so the bar only appears while the router still reports the
model as loading.

The test for this is `tests/load-test.py`, driven by the `tests/fake-load.c`
fixture: a stub router claims a model is loading while a fake `llama-server`
child does real `O_DIRECT` reads of a GGUF. No model server is involved.

## Prefill progress

While a request's prompt is being read, the same line shows how much of it has
been processed:

    LLM llama  Qwen3.5-35B-A3B-abliterated-128k
    PREF[||||||||||||||||||||||||||||||||||||        ] 95%  41.4k/43.5k tokens  3.1k tok/s  eta 1s

Unlike the load bar this is not inferred -- the router reports it directly.
`GET /slots?model=<id>` (on a router `?model=` is required; bare `/slots` answers
HTTP 400) returns the slot handling the task with `n_prompt_tokens` and
`n_prompt_tokens_processed`, and the percentage is their ratio.

The details that decide whether the figure is honest:

 - `is_processing` is true during decode too, when `processed == total`. The bar
   therefore appears only while a prompt is still being read; when it finishes the
   context bar takes the line back, rather than a bar frozen at 100%.
 - `n_prompt_tokens_cache` is shown when non-zero, so a prompt that was mostly
   reused from cache reads as a cache hit rather than as suspiciously instant.
 - Every slot is examined, not just the first, since with `--parallel > 1` the
   first may be idle while a later one is working.
 - `id_task` changes with each request and resets the rate, so a new prompt cannot
   inherit the previous one's tokens/s. Rate and ETA come from the delta between
   polls, not from timing the bar.

That one row is chosen by what the model is doing: the load bar while it is
loading, the prefill bar while a prompt is being read, otherwise the context bar
or the static facts. **No extra rows are used** -- the meter band is exactly as
tall as it was.

Tested by `tests/prefill-test.py`: four phases against a stub router (prefill in
flight, prompt finished, mostly-cached prompt, loaded but idle), one of which
serves whitespace-separated JSON so the parser stays tolerant of that.

### Why the bar is eased

The router's `n_prompt_tokens_processed` advances a whole *logical batch* at a
time (`-b`, 2048 by default), so on fast hardware the raw figure jumps about 5%
at once -- roughly every 0.66 s, and only ~21 steps for a 43k prompt. No polling
interval makes that finer, so the bar is eased towards the last value the router
reported: it then advances every frame, while the printed percentage and the
token counts stay exactly what was reported. The fill trails the number by less
than one batch, which is not visible.

`tests/ease-test.c` unit-tests that step -- monotone, never past the target,
largest single-frame move 2.25%, and a backwards value (a new request) taken
immediately instead of eased. It is a *unit* test on purpose: the UI harness
cannot see this, because it scrapes the pty stream and ncurses writes only the
characters that changed, so successive frames of one line cannot be recovered
from it.

### Poll cadence

The router is polled adaptively, because a fixed interval is wrong in both
directions. The meter is redrawn at nvtop's refresh rate (0.1 s by default), so
the old fixed 1.2 s poll is what made the bars look like they ran at about 1 fps
-- the frames were there, the numbers were not. Polling quickly also costs the
router a moment on its task lock, so polling fastest while idle is the worst
possible default.

| state | interval |
| --- | --- |
| a model is loading, or a prompt is being read | 300 ms |
| a loaded model is decoding | 900 ms |
| loaded, nothing in flight | 2 s |
| nothing loaded, or the router is not answering | 5 s |

`tests/prefill-test.py` counts the requests each phase actually serves, so the
tiers are checked rather than assumed: 6 polls per 3 s while a prompt is being
read, against 2 per 3 s when idle.

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
- `tools/colour-screen.py` — the same, but keeping each cell's foreground colour,
  so a single metric's line can be counted on its own: `--legend 'GPU1 clock%'`
  names the colour that line is drawn in, `--mask N` prints only its cells.

## Limitations

- Routers unload idle models, so `idle, none loaded` plus a preset count is a
  normal and truthful state, not a bug.
- The context bar appears only when the router serves `/metrics?model=`.
- A model is shown per row; if more are running than there are rows, the last
  row reports `+N more` rather than dropping them silently.

## Licence

GPL-3+ — these are patches against GPL-3+ nvtop; see `COPYING`.
