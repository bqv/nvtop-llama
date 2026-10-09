/* Unit test for the progress-bar easing: the counter steps, the bar must not. */
#include "nvtop/model_meter_c.h"
#include <math.h>
#include <stdio.h>

int main(void) {
  const double lo = 23.0, hi = 32.0; /* one logical batch on a 43k prompt */
  double shown = lo, prev = lo;
  int frames = 0, distinct = 1;
  double maxstep = 0;
  int bad = 0;

  while (shown < hi && frames < 60) {
    double next = nvtop_llm_ease_step(shown, hi);
    if (next < shown) { printf("  FAIL went backwards: %.2f -> %.2f\n", shown, next); bad = 1; break; }
    if (next > hi + 1e-9) { printf("  FAIL overshot the target: %.2f -> %.2f\n", shown, next); bad = 1; break; }
    if (next > prev) distinct++;
    if (next - shown > maxstep) maxstep = next - shown;
    prev = next;
    shown = next;
    frames++;
  }
  printf("  eased %.0f%% -> %.0f%% over %d frames (%d distinct values, largest single-frame move %.2f%%), ends at %.2f\n",
         lo, hi, frames, distinct, maxstep, shown);
  if (!bad && fabs(shown - hi) > 0.4) { printf("  FAIL never reached the target\n"); bad = 1; }
  if (!bad && distinct < 5) { printf("  FAIL too few intermediate values (%d): the bar would still lurch\n", distinct); bad = 1; }
  /* More distinct values is the point: the bar should move every frame, and no
   * single frame should jump far enough to look like a lurch. */
  if (!bad && frames < 5) { printf("  FAIL only %d frames of movement\n", frames); bad = 1; }
  if (!bad && maxstep > 2.5) { printf("  FAIL one frame moved %.2f%% -- still a lurch\n", maxstep); bad = 1; }
  if (!bad && nvtop_llm_ease_step(hi, hi) != hi) { printf("  FAIL moved with no new data\n"); bad = 1; }
  if (!bad && nvtop_llm_ease_step(hi, 4.0) != 4.0) { printf("  FAIL a reset was eased instead of taken\n"); bad = 1; }
  printf("%s\n", bad ? "  FAILED" : "  PASS");
  return bad;
}
