/*
 * Integration test: polls the real llama.cpp routers and prints exactly what
 * the nvtop meter will display, including every model a router has running.
 *
 * Honours NVTOP_LLM_SERVERS, so it can be pointed at a stub server.
 */
#include "nvtop/model_meter_c.h"

#include <stdio.h>

static const char *state_name(nvtop_llm_state s) {
  switch (s) {
  case NVTOP_LLM_LOADED:
    return "LOADED";
  case NVTOP_LLM_LOADING:
    return "LOADING";
  case NVTOP_LLM_IDLE:
    return "IDLE";
  default:
    return "SERVER_DOWN";
  }
}

int main(void) {
  const char *gpus[] = {"AMD Radeon RX 7900 XT", "NVIDIA GeForce GT 1030",
                        "Xeon E3-1200 v3/4th Gen Core Processor Integrated Graphics Controller"};
  unsigned n, i;

  nvtop_model_meter_poll();
  n = nvtop_model_meter_count();
  printf("servers polled: %u\n\n", n);

  for (i = 0; i < n; i++) {
    const nvtop_model_meter_info *s = nvtop_model_meter_get(i);
    if (!s) {
      printf("[%u] <null>\n", i);
      continue;
    }
    printf("[%u] %-6s gpu_hint=%-5s reachable=%s state=%-11s presets=%u running=%u\n", i, s->server_name,
           s->gpu_hint, s->reachable ? "yes" : "NO", state_name(s->state), s->preset_count, s->loaded_count);

    for (unsigned k = 0; k < s->loaded_count; k++) {
      const nvtop_llm_model *m = &s->models[k];
      printf("     [%u] %-38s %s\n", k, m->model_name, state_name(m->state));
      printf("         ctx %u (trained %u) slots %u | %llu bytes %llu params %s | kv %s/%s\n", m->ctx_size,
             m->ctx_train, m->parallel, (unsigned long long)m->model_size_bytes, (unsigned long long)m->n_params,
             m->ftype, m->kv_type_k, m->kv_type_v);
      if (m->has_live_ctx)
        printf("         live ctx: %u%%  tokens %llu/%llu\n", m->kv_usage_pct, (unsigned long long)m->kv_tokens,
               (unsigned long long)m->kv_tokens_total);
      else
        printf("         live ctx: unavailable\n");
    }
    printf("\n");
  }

  printf("GPU -> router mapping:\n");
  for (unsigned g = 0; g < sizeof(gpus) / sizeof(gpus[0]); g++) {
    const nvtop_model_meter_info *m = nvtop_model_meter_for_gpu(gpus[g]);
    printf("  %-42s -> %s\n", gpus[g], m ? m->server_name : "(no meter)");
  }

  nvtop_model_meter_shutdown();
  return 0;
}
