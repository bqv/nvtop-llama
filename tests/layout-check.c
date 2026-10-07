/*
 * Verifies the LLM meter row reservation policy:
 *   1. GPU header blocks never overlap the plot area.
 *   2. Everything stays on screen.
 *   3. The chosen meter height never drops a chart and never leaves the plot
 *      area below the layout's minimum usable chart height (7 rows).
 *
 * Uses the real compute_sizes_from_layout(); mirrors the chooser in
 * initialize_all_windows().
 */
#include "nvtop/interface_layout_selection.h"

#include <stdio.h>
#include <string.h>

#define MIN_USABLE_PLOT_ROWS 7

static int failures = 0;

static unsigned plot_area_rows(const struct window_position *pp, unsigned n) {
  unsigned top = 0, bottom = 0;
  bool first = true;
  for (unsigned i = 0; i < n; i++) {
    if (first || pp[i].posY < top)
      top = pp[i].posY;
    if (first || pp[i].posY + pp[i].sizeY > bottom)
      bottom = pp[i].posY + pp[i].sizeY;
    first = false;
  }
  return n ? bottom - top : 0;
}

static void check(unsigned rows, unsigned cols, unsigned devices, bool info_bar) {
  nvtop_interface_gpu_opts opts[8];
  struct window_position devpos[8], plotpos[MAX_CHARTS], procpos, setuppos;
  unsigned map[8], num_plots = 0, baseline_plots, llm, worst_bottom = 0, pref;
  unsigned base = info_bar ? 4u : 3u;

  memset(opts, 0, sizeof(opts));
  for (unsigned i = 0; i < devices; i++)
    opts[i].to_draw = plot_default_draw_info();

  pref = rows >= 30 ? 2u : (rows >= 22 ? 1u : 0u);

  compute_sizes_from_layout(devices, base, 83, rows - 1, cols, opts, process_default_displayed_field(), devpos,
                            &num_plots, plotpos, map, &procpos, &setuppos, false);
  baseline_plots = num_plots;

  /* mirror the chooser */
  llm = 0;
  for (unsigned candidate = pref;; candidate--) {
    compute_sizes_from_layout(devices, base + candidate, 83, rows - 1, cols, opts, process_default_displayed_field(),
                              devpos, &num_plots, plotpos, map, &procpos, &setuppos, false);
    if (candidate == 0 || (num_plots >= baseline_plots && plot_area_rows(plotpos, num_plots) >= MIN_USABLE_PLOT_ROWS)) {
      llm = candidate;
      break;
    }
  }

  for (unsigned i = 0; i < devices; i++) {
    unsigned bottom = devpos[i].posY + base + llm;
    if (bottom > worst_bottom)
      worst_bottom = bottom;
    if (bottom > rows - 1) {
      printf("  FAIL dev %u ends at row %u > last row %u\n", i, bottom, rows - 1);
      failures++;
    }
  }
  for (unsigned p = 0; p < num_plots; p++) {
    if (plotpos[p].posY < worst_bottom) {
      printf("  FAIL OVERLAP: plot %u at row %u, header ends at %u\n", p, plotpos[p].posY, worst_bottom);
      failures++;
    }
  }
  if (num_plots < baseline_plots) {
    printf("  FAIL lost charts: %u with meter vs %u without\n", num_plots, baseline_plots);
    failures++;
  }
  if (num_plots > 0 && plot_area_rows(plotpos, num_plots) < MIN_USABLE_PLOT_ROWS) {
    printf("  FAIL plot area %u below usable minimum\n", plot_area_rows(plotpos, num_plots));
    failures++;
  }

  printf("  %3ux%-4u devs=%u bar=%d pref=%u -> llm=%u charts=%u (baseline %u) plot_area=%2u header_ends=%u\n", rows,
         cols, devices, info_bar, pref, llm, num_plots, baseline_plots, plot_area_rows(plotpos, num_plots),
         worst_bottom);
}

int main(void) {
  printf("roomy terminals (the user's case):\n");
  check(60, 240, 3, true);
  check(50, 200, 3, false);
  check(50, 200, 3, true);
  check(45, 160, 3, false);
  check(42, 150, 3, false);
  check(40, 120, 3, false);

  printf("\ncramped terminals - must fall back, not lose the graphs:\n");
  check(34, 100, 3, true);
  check(30, 100, 3, false);
  check(26, 90, 3, false);
  check(24, 120, 3, false);
  check(20, 100, 3, false);
  check(16, 80, 3, false);

  printf("\nfewer devices:\n");
  check(50, 200, 2, false);
  check(40, 120, 2, false);
  check(30, 120, 2, false);
  check(50, 200, 1, false);
  check(20, 100, 1, false);

  printf("\nresize stability:\n");
  for (int i = 0; i < 3; i++)
    check(50, 200, 3, false);

  if (failures) {
    printf("\n%d FAILURE(S)\n", failures);
    return 1;
  }
  printf("\nOK: no overlap, nothing off-screen, charts preserved and usable\n");
  return 0;
}
