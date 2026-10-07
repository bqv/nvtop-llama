/*
 * Verifies the nvtop header/meter layout:
 *   1. device bands never overlap the plot area, and stay on screen
 *   2. the chosen meter height never drops a chart or starves the plot area
 *   3. at most ONE blank row separates consecutive device bands
 *
 * (3) is the user-visible rule: a GPU whose block has no meter band must not
 * reserve rows it never draws into. Device 0 here has no router, like the iGPU.
 *
 * Uses the real compute_sizes_from_layout(); mirrors the chooser in
 * initialize_all_windows().
 */
#include "nvtop/interface_layout_selection.h"

#include <stdio.h>
#include <string.h>

#define MIN_USABLE_PLOT_ROWS 7
#define MAX_DEV 8

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

static void check(unsigned rows, unsigned cols, unsigned devices, bool info_bar, unsigned devcols) {
  nvtop_interface_gpu_opts opts[MAX_DEV];
  struct window_position devpos[MAX_DEV], plotpos[MAX_CHARTS], procpos, setuppos;
  unsigned map[MAX_DEV], num_plots = 0, baseline_plots, pref;
  unsigned extras[MAX_DEV];
  unsigned base = info_bar ? 4u : 3u;

  memset(opts, 0, sizeof(opts));
  for (unsigned i = 0; i < devices; i++)
    opts[i].to_draw = plot_default_draw_info();

  pref = rows >= 30 ? 2u : (rows >= 22 ? 1u : 0u);

  /* baseline: nobody has a meter */
  for (unsigned i = 0; i < devices; i++)
    extras[i] = 0;
  compute_sizes_from_layout(devices, base, devcols, rows - 1, cols, opts, process_default_displayed_field(), extras,
                            devpos, &num_plots, plotpos, map, &procpos, &setuppos, false);
  baseline_plots = num_plots;

  /* chooser mirror: device 0 has no router, the rest do */
  for (unsigned candidate = pref;; candidate--) {
    for (unsigned i = 0; i < devices; i++)
      extras[i] = (i == 0) ? 0 : candidate;
    compute_sizes_from_layout(devices, base, devcols, rows - 1, cols, opts, process_default_displayed_field(), extras,
                              devpos, &num_plots, plotpos, map, &procpos, &setuppos, false);
    if (candidate == 0 || (num_plots >= baseline_plots && plot_area_rows(plotpos, num_plots) >= MIN_USABLE_PLOT_ROWS))
      break;
  }

  /* 1+3: bands on screen, and at most one blank row between them */
  unsigned worst_bottom = 0;
  for (unsigned i = 0; i < devices; i++) {
    unsigned bottom = devpos[i].posY + devpos[i].sizeY;
    if (bottom > worst_bottom)
      worst_bottom = bottom;
    if (bottom > rows - 1) {
      printf("  FAIL dev %u ends at row %u > last usable row %u\n", i, bottom, rows - 1);
      failures++;
    }
  }

  /* Gaps are between ROWS of bands: devices sharing a row share a height, so a
   * row must be measured as a whole. */
  {
    unsigned row_top[MAX_DEV], row_bottom[MAX_DEV], nrows = 0;
    for (unsigned i = 0; i < devices; i++) {
      unsigned r = nrows;
      for (unsigned k = 0; k < nrows; k++)
        if (row_top[k] == devpos[i].posY) { r = k; break; }
      if (r == nrows) { row_top[nrows] = devpos[i].posY; row_bottom[nrows] = 0; nrows++; }
      unsigned b = devpos[i].posY + devpos[i].sizeY;
      if (b > row_bottom[r]) row_bottom[r] = b;
    }
    for (unsigned r = 0; r + 1 < nrows; r++) {
      unsigned gap = row_top[r + 1] - row_bottom[r];
      if (gap > 1) {
        printf("  FAIL gap of %u blank rows between band row %u and %u (max 1)\n", gap, r, r + 1);
        failures++;
      }
    }
  }
  for (unsigned p = 0; p < num_plots; p++) {
    if (plotpos[p].posY < worst_bottom) {
      printf("  FAIL OVERLAP: plot %u at row %u, bands end at %u\n", p, plotpos[p].posY, worst_bottom);
      failures++;
    }
  }
  if (num_plots < baseline_plots) {
    printf("  FAIL lost charts: %u with meter vs %u without\n", num_plots, baseline_plots);
    failures++;
  }

  printf("  %3ux%-4u dc=%u devs=%u bar=%d pref=%u -> extras[0]=%u extras[1]=%u charts=%u plot_area=%2u bands_end=%u\n",
         rows, cols, devcols, devices, info_bar, pref, extras[0], extras[1], num_plots,
         plot_area_rows(plotpos, num_plots), worst_bottom);
}

int main(void) {
  printf("3 GPUs, device 0 has no router (iGPU), 129-col blocks:\n");
  check(50, 200, 3, true, 129);
  check(50, 200, 3, false, 129);
  check(60, 300, 3, true, 129);
  check(45, 200, 3, false, 129);
  check(40, 160, 3, false, 129);
  check(34, 140, 3, true, 129);
  check(50, 400, 3, false, 129);

  printf("\nnarrow blocks (83 cols):\n");
  check(50, 200, 3, false, 83);
  check(40, 120, 3, false, 83);
  check(30, 100, 3, false, 83);

  printf("\nall devices have routers:\n");
  check(50, 200, 2, false, 129);

  if (failures) {
    printf("\n%d FAILURE(S)\n", failures);
    return 1;
  }
  printf("\nOK: no overlap, on-screen, charts kept, and gaps never exceed one row\n");
  return 0;
}
