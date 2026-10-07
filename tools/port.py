#!/usr/bin/env python3
"""
Port the nvtop LLM meter feature onto a pristine nvtop source tree.

Usage: port.py <target-tree>

Three source shapes are handled, detected from the tree:
  modern  - 3.2.0 / 3.3.2 release layout  (process_win_hide, exec_engines row)
  legacy  - 3.1.0                          (no process_win_hide argument)
  master  - post-3.3.2 development         (extra_info[] grid, options arg)

Every anchor is asserted, so an unrecognised tree fails loudly rather than
silently producing broken code.
"""
import os
import shutil
import sys

MINE = "/home/user/tmp/nvtop"


def sub_once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"FAIL [{label}]: anchor found {n} times, expected 1:\n---\n{old[:400]}\n---")
    return text.replace(old, new, 1)


def extract_func(lines, comment_marker):
    """From the comment above a function through its closing brace."""
    s = next(i for i, l in enumerate(lines) if comment_marker in l)
    f = next(i for i in range(s, len(lines)) if lines[i].startswith("static "))
    depth = 0
    started = False
    for i in range(f, len(lines)):
        if "{" in lines[i]:
            started = True
        depth += lines[i].count("{") - lines[i].count("}")
        if started and depth == 0:
            return "\n".join(lines[s:i + 1])
    raise SystemExit(f"unbalanced braces for {comment_marker}")


def extract_meter_block(lines):
    """The self-contained meter rendering section, up to draw_devices()."""
    mi = next(i for i, l in enumerate(lines) if l.startswith("/* LLM model meter"))
    start = mi - 1
    assert lines[start].lstrip().startswith("/* ---"), "expected a dashed separator above the meter section"
    end = next(i for i, l in enumerate(lines) if l.startswith("static void draw_devices("))
    return "\n".join(lines[start:end]).rstrip("\n")


def detect(target):
    h = open(os.path.join(target, "include/nvtop/interface_internal_common.h")).read()
    c = open(os.path.join(target, "src/interface.c")).read()
    if "extra_info[MAX_EXTRA_INFO_ITEMS]" in h:
        return "master", h, c
    return ("modern" if "hide_processes_list);" in c else "legacy"), h, c



def gap_fix(target, variant):
    """Reserve meter rows per GPU, not per layout, and drop the empty `LLM -`
    band.

    Without this, every header stack reserves the same meter height, so a GPU
    with no router (the iGPU driving the displays) leaves the rows it never
    draws into blank -- visible as a gap between device sections.
    """
    # ---- interface_layout_selection.h : per-device extra rows --------------
    h = os.path.join(target, "include/nvtop/interface_layout_selection.h")
    t = open(h).read()
    t = sub_once(t, "                               process_field_displayed process_field_displayed,\n",
                 "                               process_field_displayed process_field_displayed,\n"
                 "                               const unsigned *device_extra_rows,\n", "layout proto")
    open(h, "w").write(t)

    # ---- interface_layout_selection.c ---------------------------------------
    c = os.path.join(target, "src/interface_layout_selection.c")
    t = open(c).read()
    t = sub_once(t, "process_field_displayed process_displayed, struct window_position *device_positions,",
                 "process_field_displayed process_displayed, const unsigned *device_extra_rows,\n"
                 "                               struct window_position *device_positions,", "layout def")
    t = sub_once(t, "  min_rows_for_header = header_stacks * device_header_rows;",
                 "  // Extra rows (the LLM meter) are counted per header stack, and a stack is only\n"
                 "  // as tall as its tallest member. A GPU with no meter of its own therefore\n"
                 "  // reserves nothing, instead of leaving the rows it never draws into blank.\n"
                 "  unsigned stack_extra_rows[MAX_CHARTS];\n"
                 "  unsigned total_extra_rows = 0;\n"
                 "  for (unsigned s = 0; s < header_stacks && s < MAX_CHARTS; ++s)\n"
                 "    stack_extra_rows[s] = 0;\n"
                 "  for (unsigned i = 0; i < devices_count; ++i) {\n"
                 "    unsigned stack = i / num_device_per_row;\n"
                 "    unsigned extra = device_extra_rows ? device_extra_rows[i] : 0;\n"
                 "    if (stack < header_stacks && stack < MAX_CHARTS && extra > stack_extra_rows[stack])\n"
                 "      stack_extra_rows[stack] = extra;\n"
                 "  }\n"
                 "  for (unsigned s = 0; s < header_stacks && s < MAX_CHARTS; ++s)\n"
                 "    total_extra_rows += stack_extra_rows[s];\n"
                 "\n"
                 "  min_rows_for_header = header_stacks * device_header_rows + total_extra_rows;",
                 "layout header rows")
    t = sub_once(t,
                 "  unsigned num_this_row = 0;\n  unsigned headerPosX = space_before_header;\n"
                 "  unsigned headerPosY = 0;\n  for (unsigned i = 0; i < devices_count; ++i) {\n"
                 "    device_positions[i].posX = headerPosX;\n    device_positions[i].posY = headerPosY;\n"
                 "    device_positions[i].sizeX = device_header_cols;\n    device_positions[i].sizeY = device_header_rows;\n"
                 "    num_this_row++;\n    if (num_this_row == num_device_per_row) {\n"
                 "      headerPosX = space_before_header;\n"
                 "      headerPosY += device_header_rows + space_between_header_stack;\n      num_this_row = 0;\n"
                 "    } else {\n      headerPosX += device_header_cols + space_between_header_col;\n    }\n  }",
                 "  unsigned num_this_row = 0;\n  unsigned headerPosX = space_before_header;\n"
                 "  unsigned headerPosY = 0;\n  unsigned current_stack = 0;\n"
                 "  for (unsigned i = 0; i < devices_count; ++i) {\n"
                 "    unsigned extra = device_extra_rows ? device_extra_rows[i] : 0;\n"
                 "    device_positions[i].posX = headerPosX;\n    device_positions[i].posY = headerPosY;\n"
                 "    device_positions[i].sizeX = device_header_cols;\n"
                 "    device_positions[i].sizeY = device_header_rows + extra;\n"
                 "    num_this_row++;\n    if (num_this_row == num_device_per_row) {\n"
                 "      headerPosX = space_before_header;\n"
                 "      headerPosY += device_header_rows + space_between_header_stack;\n"
                 "      if (current_stack < MAX_CHARTS)\n        headerPosY += stack_extra_rows[current_stack];\n"
                 "      current_stack++;\n      num_this_row = 0;\n"
                 "    } else {\n      headerPosX += device_header_cols + space_between_header_col;\n    }\n  }",
                 "layout position loop")
    open(c, "w").write(t)

    # ---- poller + header: router test that does not need a poll ------------
    po = os.path.join(target, "src/llm_poller.c")
    t = open(po).read()
    anchor = "const nvtop_model_meter_info *nvtop_model_meter_for_gpu(const char *gpu_name) {"
    t = sub_once(t, anchor,
                 "/* Answered from the static server config, so it is usable while the layout is\n"
                 " * being computed, before any server has been contacted. */\n"
                 "bool nvtop_model_meter_has_router_for_gpu(const char *gpu_name) {\n"
                 "  servers_init();\n  if (!gpu_name || !gpu_name[0])\n    return false;\n"
                 "  for (unsigned i = 0; i < g_def_count && i < NVTOP_LLM_MAX_SERVERS; i++) {\n"
                 "    if (g_defs[i].gpu_hint[0] && contains_nocase(gpu_name, g_defs[i].gpu_hint))\n"
                 "      return true;\n  }\n  return false;\n}\n\n" + anchor, "router check")
    open(po, "w").write(t)

    mh = os.path.join(target, "include/nvtop/model_meter_c.h")
    t = open(mh).read()
    t = sub_once(t, "void nvtop_model_meter_shutdown(void);",
                 "/* True when a router is configured for this GPU name. Does not poll, so it can\n"
                 " * be asked while the display is being laid out. */\n"
                 "bool nvtop_model_meter_has_router_for_gpu(const char *gpu_name);\n\n"
                 "void nvtop_model_meter_shutdown(void);", "router decl")
    open(mh, "w").write(t)

    # ---- interface.c: helper signature, chooser, dropped LLM - band --------
    p2 = os.path.join(target, "src/interface.c")
    t = open(p2).read()
    t = sub_once(t, "                                         unsigned rows, unsigned cols, struct window_position *device_positions,",
                 "                                         unsigned rows, unsigned cols, const unsigned *device_extra_rows,\n"
                 "                                         struct window_position *device_positions,", "helper sig")
    t = sub_once(t, "dwin->options.process_fields_displayed, device_positions, &dwin->num_plots, plot_positions,",
                 "dwin->options.process_fields_displayed, device_extra_rows, device_positions, &dwin->num_plots, plot_positions,",
                 "helper call")
    t = sub_once(t, "static void initialize_all_windows(struct nvtop_interface *dwin) {",
                 "/* True when a router is configured for this GPU. Independent of the poll, so it\n"
                 " * can be asked while the layout is being computed. */\n"
                 "static bool gpu_has_llm_router(struct nvtop_interface *dwin, unsigned dev_id) {\n"
                 "  struct gpu_info *gpu = dwin->options.gpu_specific_opts[dev_id].linkedGpu;\n"
                 "  return gpu && GPUINFO_STATIC_FIELD_VALID(&gpu->static_info, device_name) &&\n"
                 "         nvtop_model_meter_has_router_for_gpu(gpu->static_info.device_name);\n"
                 "}\n\nstatic void initialize_all_windows(struct nvtop_interface *dwin) {", "router helper")

    rows_expr = "device_rows" if variant == "master" else "base_header_rows"
    alloc_call_old = ("    alloc_device_window(device_positions[i].posY, device_positions[i].posX, "
                      "device_positions[i].sizeX, base_header_rows,\n                        llm_rows, &dwin->devices_win[i]);")
    alloc_call_new = ("    alloc_device_window(device_positions[i].posY, device_positions[i].posX, "
                      "device_positions[i].sizeX, base_header_rows,\n                        device_extra_rows[i], "
                      "&dwin->devices_win[i]);")
    if variant == "master":
        alloc_call_old = ("    alloc_device_window(device_positions[i].posY, device_positions[i].posX, "
                          "device_positions[i].sizeX, &dwin->options,\n                        "
                          "dwin->extra_info_rows, llm_rows, &dwin->devices_win[i]);")
        alloc_call_new = ("    alloc_device_window(device_positions[i].posY, device_positions[i].posX, "
                          "device_positions[i].sizeX, &dwin->options,\n                        "
                          "dwin->extra_info_rows, device_extra_rows[i], &dwin->devices_win[i]);")

    t = sub_once(t, "  unsigned int llm_rows = 0;\n  unsigned int baseline_plots;",
                 "  unsigned int baseline_plots;\n\n"
                 "  /* Meter rows are reserved per GPU, and only for GPUs that have a router: one\n"
                 "   * without one reserves nothing, so it cannot leave a blank band behind. */\n"
                 "  unsigned int device_extra_rows[devices_count];\n"
                 "  for (unsigned int i = 0; i < devices_count; ++i)\n    device_extra_rows[i] = 0;",
                 "extras array")
    t = sub_once(t, "  for (unsigned int candidate = llm_rows_pref;; candidate--) {\n"
                    "    compute_layout_for_interface(dwin, devices_count, " + rows_expr + " + candidate, rows, cols, device_positions,\n"
                    "                                 plot_positions, map_device_to_plot, &process_position, &setup_position);\n"
                    "    if (candidate == 0 || (dwin->num_plots >= baseline_plots && plot_area_rows(plot_positions, dwin->num_plots) >= 7)) {\n"
                    "      llm_rows = candidate;\n      break;\n    }\n  }",
                 "  for (unsigned int candidate = llm_rows_pref;; candidate--) {\n"
                 "    for (unsigned int i = 0; i < devices_count; ++i)\n"
                 "      device_extra_rows[i] = gpu_has_llm_router(dwin, i) ? candidate : 0;\n"
                 "    compute_layout_for_interface(dwin, devices_count, " + rows_expr + ", rows, cols, device_extra_rows, device_positions,\n"
                 "                                 plot_positions, map_device_to_plot, &process_position, &setup_position);\n"
                 "    if (candidate == 0 || (dwin->num_plots >= baseline_plots && plot_area_rows(plot_positions, dwin->num_plots) >= 7))\n"
                 "      break;\n  }", "per-device chooser")
    t = sub_once(t, "compute_layout_for_interface(dwin, devices_count, " + rows_expr + ", rows, cols, device_positions, plot_positions,\n"
                    "                               map_device_to_plot, &process_position, &setup_position);\n"
                    "  baseline_plots = dwin->num_plots;",
                 "compute_layout_for_interface(dwin, devices_count, " + rows_expr + ", rows, cols, device_extra_rows, device_positions,\n"
                 "                               plot_positions, map_device_to_plot, &process_position, &setup_position);\n"
                 "  baseline_plots = dwin->num_plots;", "baseline call")
    t = sub_once(t, alloc_call_old, alloc_call_new, "alloc call")
    band_old_commented = """      if (llm) {
        draw_llm_meter(dev->llm_meter_win, llm);
      } else {
        /* No LLM router is mapped to this GPU (e.g. the iGPU driving the
         * displays): say so rather than leaving a mysterious blank gap. */
        werase(dev->llm_meter_win);
        wcolor_set(dev->llm_meter_win, cyan_color, NULL);
        mvwprintw(dev->llm_meter_win, 0, 0, "LLM");
        wstandend(dev->llm_meter_win);
        mvwprintw(dev->llm_meter_win, 0, 3, " -");
        wnoutrefresh(dev->llm_meter_win);
      }"""
    band_old_plain = """      if (llm) {
        draw_llm_meter(dev->llm_meter_win, llm);
      } else {
        werase(dev->llm_meter_win);
        wcolor_set(dev->llm_meter_win, cyan_color, NULL);
        mvwprintw(dev->llm_meter_win, 0, 0, "LLM");
        wstandend(dev->llm_meter_win);
        mvwprintw(dev->llm_meter_win, 0, 3, " -");
        wnoutrefresh(dev->llm_meter_win);
      }"""
    band_new = """      if (llm)
        draw_llm_meter(dev->llm_meter_win, llm);
      else
        werase(dev->llm_meter_win);"""
    if t.count(band_old_commented) == 1:
        t = t.replace(band_old_commented, band_new, 1)
    else:
        t = sub_once(t, band_old_plain, band_new, "drop empty LLM - band")
    open(p2, "w").write(t)
    print("  gap fix applied")



def main():
    target = sys.argv[1]
    variant, hdr, iface = detect(target)
    mine_lines = open(os.path.join(MINE, "src/interface.c")).read().split("\n")
    meter_block = extract_meter_block(mine_lines)
    plot_area_fn = extract_func(mine_lines, "/* Real vertical extent of the plot area.")

    # ---- new files, byte-identical across versions -------------------------
    shutil.copy(os.path.join(MINE, "src/llm_poller.c"), os.path.join(target, "src/llm_poller.c"))
    shutil.copy(os.path.join(MINE, "include/nvtop/model_meter_c.h"),
                os.path.join(target, "include/nvtop/model_meter_c.h"))

    # ---- include/nvtop/interface_internal_common.h -------------------------
    p = os.path.join(target, "include/nvtop/interface_internal_common.h")
    t = open(p).read()
    if variant == "master":
        t = sub_once(t, "  WINDOW *extra_info[MAX_EXTRA_INFO_ITEMS];\n",
                     "  WINDOW *extra_info[MAX_EXTRA_INFO_ITEMS];\n"
                     "  WINDOW *llm_meter_win; // LLM model meter display\n", "device_window field")
    else:
        t = sub_once(t, "  WINDOW *exec_engines;\n",
                     "  WINDOW *exec_engines;\n  WINDOW *llm_meter_win; // LLM model meter display\n",
                     "device_window field")
    open(p, "w").write(t)

    # ---- src/interface.c ---------------------------------------------------
    p = os.path.join(target, "src/interface.c")
    t = open(p).read()

    # Meter row expression and allocation signature differ on master.
    if variant == "master":
        meter_row_expr = "start_row + 3 + extra_info_rows"
        alloc_sig_old = ("static void alloc_device_window(unsigned int start_row, unsigned int start_col, "
                         "unsigned int totalcol,\n"
                         "                                const nvtop_interface_option *options, "
                         "unsigned int extra_info_rows,\n"
                         "                                struct device_window *dwin) {")
        alloc_sig_new = ("static void alloc_device_window(unsigned int start_row, unsigned int start_col, "
                         "unsigned int totalcol,\n"
                         "                                const nvtop_interface_option *options, "
                         "unsigned int extra_info_rows, unsigned int llm_rows,\n"
                         "                                struct device_window *dwin) {")
        free_anchor = ("  for (unsigned int i = 0; i < MAX_EXTRA_INFO_ITEMS; ++i) {\n"
                       "    if (dwin->extra_info[i])\n      delwin(dwin->extra_info[i]);\n  }\n}")
        free_new = ("  for (unsigned int i = 0; i < MAX_EXTRA_INFO_ITEMS; ++i) {\n"
                    "    if (dwin->extra_info[i])\n      delwin(dwin->extra_info[i]);\n  }\n"
                    "  if (dwin->llm_meter_win)\n    delwin(dwin->llm_meter_win);\n}")
        draw_anchor = ("      wnoutrefresh(dev->extra_info[extra_slot]);\n    }\n\n    dev_id++;\n  }\n}")
        draw_new = ("      wnoutrefresh(dev->extra_info[extra_slot]);\n    }\n"
                    + ""
                    )  # replaced below
        poll_anchor = ("  update_extra_info_rows(devices, interface);\n"
                       "  draw_devices(devices, interface);")
        poll_new = ("  /* Refresh the LLM router state (no-op if polled recently). */\n"
                    "  nvtop_model_meter_poll();\n\n"
                    "  update_extra_info_rows(devices, interface);\n"
                    "  draw_devices(devices, interface);")
    else:
        meter_row_expr = "start_row + base_rows"
        alloc_sig_old = ("static void alloc_device_window(unsigned int start_row, unsigned int start_col, "
                         "unsigned int totalcol,\n"
                         "                                struct device_window *dwin) {")
        alloc_sig_new = ("static void alloc_device_window(unsigned int start_row, unsigned int start_col, "
                         "unsigned int totalcol,\n"
                         "                                unsigned int base_rows, unsigned int llm_rows, "
                         "struct device_window *dwin) {")
        free_anchor = "  delwin(dwin->pcie_info);\n}"
        free_new = ("  delwin(dwin->pcie_info);\n  if (dwin->llm_meter_win)\n    delwin(dwin->llm_meter_win);\n}")
        draw_anchor = "      wnoutrefresh(dev->exec_engines);\n    }\n\n    dev_id++;\n  }\n}"
        draw_new = None  # replaced below
        poll_anchor = ("void draw_gpu_info_ncurses(unsigned devices_count, struct list_head *devices, "
                       "struct nvtop_interface *interface) {\n\n  draw_devices(devices, interface);")
        poll_new = ("void draw_gpu_info_ncurses(unsigned devices_count, struct list_head *devices, "
                    "struct nvtop_interface *interface) {\n\n"
                    "  /* Refresh the LLM router state (no-op if polled recently). */\n"
                    "  nvtop_model_meter_poll();\n\n  draw_devices(devices, interface);")

    # The meter draw call, shared wording, anchored per variant.
    if draw_new is None:
        draw_new = (draw_anchor.split("\n\n    dev_id++")[0] + "\n\n"
                    "    /* LLM model meter, on the rows reserved by the layout below this GPU */\n"
                    "    if (dev->llm_meter_win) {\n"
                    "      const nvtop_model_meter_info *llm = NULL;\n"
                    "      if (GPUINFO_STATIC_FIELD_VALID(&device->static_info, device_name))\n"
                    "        llm = nvtop_model_meter_for_gpu(device->static_info.device_name);\n"
                    "      if (llm) {\n"
                    "        draw_llm_meter(dev->llm_meter_win, llm);\n"
                    "      } else {\n"
                    "        /* No LLM router is mapped to this GPU (e.g. the iGPU driving the\n"
                    "         * displays): say so rather than leaving a mysterious blank gap. */\n"
                    "        werase(dev->llm_meter_win);\n"
                    "        wcolor_set(dev->llm_meter_win, cyan_color, NULL);\n"
                    "        mvwprintw(dev->llm_meter_win, 0, 0, \"LLM\");\n"
                    "        wstandend(dev->llm_meter_win);\n"
                    "        mvwprintw(dev->llm_meter_win, 0, 3, \" -\");\n"
                    "        wnoutrefresh(dev->llm_meter_win);\n"
                    "      }\n"
                    "    }\n\n    dev_id++;\n  }\n}")
    else:
        draw_new = (draw_anchor.split("\n\n    dev_id++")[0] + "\n\n"
                    "    /* LLM model meter, on the rows reserved by the layout below this GPU */\n"
                    "    if (dev->llm_meter_win) {\n"
                    "      const nvtop_model_meter_info *llm = NULL;\n"
                    "      if (GPUINFO_STATIC_FIELD_VALID(&device->static_info, device_name))\n"
                    "        llm = nvtop_model_meter_for_gpu(device->static_info.device_name);\n"
                    "      if (llm) {\n"
                    "        draw_llm_meter(dev->llm_meter_win, llm);\n"
                    "      } else {\n"
                    "        werase(dev->llm_meter_win);\n"
                    "        wcolor_set(dev->llm_meter_win, cyan_color, NULL);\n"
                    "        mvwprintw(dev->llm_meter_win, 0, 0, \"LLM\");\n"
                    "        wstandend(dev->llm_meter_win);\n"
                    "        mvwprintw(dev->llm_meter_win, 0, 3, \" -\");\n"
                    "        wnoutrefresh(dev->llm_meter_win);\n"
                    "      }\n"
                    "    }\n\n    dev_id++;\n  }\n}")

    layout_helper = (
        "/* One layout pass. Called a few times at startup to pick how much room the LLM\n"
        " * meter may take without costing the plots anything. */\n"
        "static void compute_layout_for_interface(struct nvtop_interface *dwin, unsigned devices_count, "
        "unsigned header_rows,\n"
        "                                         unsigned rows, unsigned cols, "
        "struct window_position *device_positions,\n"
        "                                         struct window_position plot_positions[MAX_CHARTS],\n"
        "                                         unsigned *map_device_to_plot, "
        "struct window_position *process_position,\n"
        "                                         struct window_position *setup_position) {\n"
        "  compute_sizes_from_layout(devices_count, header_rows, device_length(), rows - 1, cols, "
        "dwin->options.gpu_specific_opts,\n"
        "                            dwin->options.process_fields_displayed, device_positions, &dwin->num_plots, "
        "plot_positions,\n"
        "                            map_device_to_plot, process_position, setup_position"
        + (", dwin->options.hide_processes_list);\n" if variant != "legacy" else ");\n")
        + "}"
    )

    # layout anchor: from the device-rows computation through the alloc loop
    if variant == "master":
        layout_anchor = (
            "  unsigned int device_rows = 3 + dwin->extra_info_rows;\n"
            "  compute_sizes_from_layout(devices_count, device_rows, device_length(), rows - 1, cols,\n"
            "                            dwin->options.gpu_specific_opts, dwin->options.process_fields_displayed, "
            "device_positions,\n"
            "                            &dwin->num_plots, plot_positions, map_device_to_plot, &process_position, "
            "&setup_position,\n"
            "                            dwin->options.hide_processes_list);\n"
            "\n  alloc_plot_window(devices_count, plot_positions, map_device_to_plot, dwin);\n"
            "\n  for (unsigned int i = 0; i < devices_count; ++i) {\n"
            "    alloc_device_window(device_positions[i].posY, device_positions[i].posX, "
            "device_positions[i].sizeX, &dwin->options,\n"
            "                        dwin->extra_info_rows, &dwin->devices_win[i]);\n  }")
        layout_new = (
            "  unsigned int device_rows = 3 + dwin->extra_info_rows;\n"
            "  unsigned int llm_rows_pref = rows >= 30 ? 2u : (rows >= 22 ? 1u : 0u);\n"
            "  unsigned int llm_rows = 0;\n"
            "  unsigned int baseline_plots;\n"
            "\n"
            "  compute_layout_for_interface(dwin, devices_count, device_rows, rows, cols, device_positions, "
            "plot_positions,\n"
            "                               map_device_to_plot, &process_position, &setup_position);\n"
            "  baseline_plots = dwin->num_plots;\n"
            "\n"
            "  /* Reserve rows for the LLM model meter, but only as many as the plots can\n"
            "   * spare: take the tallest meter that keeps every chart and leaves a usable\n"
            "   * plot area, falling back a row at a time and finally to no meter at all\n"
            "   * rather than losing the graphs. */\n"
            "  for (unsigned int candidate = llm_rows_pref;; candidate--) {\n"
            "    compute_layout_for_interface(dwin, devices_count, device_rows + candidate, rows, cols, "
            "device_positions,\n"
            "                                 plot_positions, map_device_to_plot, &process_position, &setup_position);\n"
            "    if (candidate == 0 || (dwin->num_plots >= baseline_plots && "
            "plot_area_rows(plot_positions, dwin->num_plots) >= 7)) {\n"
            "      llm_rows = candidate;\n"
            "      break;\n"
            "    }\n"
            "  }\n"
            "\n  alloc_plot_window(devices_count, plot_positions, map_device_to_plot, dwin);\n"
            "\n  for (unsigned int i = 0; i < devices_count; ++i) {\n"
            "    alloc_device_window(device_positions[i].posY, device_positions[i].posX, "
            "device_positions[i].sizeX, &dwin->options,\n"
            "                        dwin->extra_info_rows, llm_rows, &dwin->devices_win[i]);\n  }")
    else:
        hide = ",\n                            dwin->options.hide_processes_list" if variant != "legacy" else ""
        fields = ("dwin->options.process_fields_displayed, device_positions,\n"
                  "                            &dwin->num_plots, plot_positions, map_device_to_plot, "
                  "&process_position, &setup_position" if variant != "legacy" else
                  "dwin->options.process_fields_displayed,\n"
                  "                            device_positions, &dwin->num_plots, plot_positions,\n"
                  "                            map_device_to_plot, &process_position, &setup_position")
        layout_anchor = (
            "  compute_sizes_from_layout(devices_count, dwin->options.has_gpu_info_bar ? 4 : 3, "
            "device_length(), rows - 1, cols,\n"
            "                            dwin->options.gpu_specific_opts, " + fields + hide + ");\n"
            "\n  alloc_plot_window(devices_count, plot_positions, map_device_to_plot, dwin);\n"
            "\n  for (unsigned int i = 0; i < devices_count; ++i) {\n"
            "    alloc_device_window(device_positions[i].posY, device_positions[i].posX, "
            "device_positions[i].sizeX,\n"
            "                        &dwin->devices_win[i]);\n  }")
        layout_new = (
            "  // Rows each GPU block really needs: 3 lines, plus a 4th for the info bar.\n"
            "  unsigned int base_header_rows = dwin->options.has_gpu_info_bar ? 4 : 3;\n"
            "  unsigned int llm_rows_pref = rows >= 30 ? 2u : (rows >= 22 ? 1u : 0u);\n"
            "  unsigned int llm_rows = 0;\n"
            "  unsigned int baseline_plots;\n"
            "\n"
            "  compute_layout_for_interface(dwin, devices_count, base_header_rows, rows, cols, device_positions, "
            "plot_positions,\n"
            "                               map_device_to_plot, &process_position, &setup_position);\n"
            "  baseline_plots = dwin->num_plots;\n"
            "\n"
            "  /* Reserve rows for the LLM model meter, but only as many as the plots can\n"
            "   * spare: take the tallest meter that keeps every chart and leaves a usable\n"
            "   * plot area, falling back a row at a time and finally to no meter at all\n"
            "   * rather than losing the graphs. */\n"
            "  for (unsigned int candidate = llm_rows_pref;; candidate--) {\n"
            "    compute_layout_for_interface(dwin, devices_count, base_header_rows + candidate, rows, cols, "
            "device_positions,\n"
            "                                 plot_positions, map_device_to_plot, &process_position, &setup_position);\n"
            "    if (candidate == 0 || (dwin->num_plots >= baseline_plots && "
            "plot_area_rows(plot_positions, dwin->num_plots) >= 7)) {\n"
            "      llm_rows = candidate;\n"
            "      break;\n"
            "    }\n"
            "  }\n"
            "\n  alloc_plot_window(devices_count, plot_positions, map_device_to_plot, dwin);\n"
            "\n  for (unsigned int i = 0; i < devices_count; ++i) {\n"
            "    alloc_device_window(device_positions[i].posY, device_positions[i].posX, "
            "device_positions[i].sizeX, base_header_rows,\n"
            "                        llm_rows, &dwin->devices_win[i]);\n  }")

    t = sub_once(t, '#include "nvtop/interface_setup_win.h"\n',
                 '#include "nvtop/interface_setup_win.h"\n#include "nvtop/model_meter_c.h"\n', "interface.h include")
    if "#include <stdarg.h>" not in t:
        t = sub_once(t, "#include <signal.h>\n", "#include <signal.h>\n#include <stdarg.h>\n", "stdarg include")

    t = sub_once(t, alloc_sig_old, alloc_sig_new, "alloc_device_window signature")

    t = sub_once(t, "  return;\nalloc_error:",
                 "  // LLM model meter gets its own window below the GPU lines. It must NOT be a\n"
                 "  // subwindow: every line above is exactly one row tall, so a subwindow taller\n"
                 "  // than its parent spills over the plots. The layout reserves llm_rows for it.\n"
                 "  dwin->llm_meter_win = NULL;\n"
                 "  if (llm_rows > 0 && totalcol > 24) {\n"
                 "    unsigned int meter_row = " + meter_row_expr + ";\n"
                 "    int term_rows, term_cols;\n"
                 "    getmaxyx(stdscr, term_rows, term_cols);\n"
                 "    (void)term_cols;\n"
                 "    if ((int)(meter_row + llm_rows) <= term_rows)\n"
                 "      dwin->llm_meter_win = newwin(llm_rows, totalcol, meter_row, start_col);\n"
                 "  }\n"
                 "\n  return;\nalloc_error:", "meter window allocation")

    t = sub_once(t, free_anchor, free_new, "free_device_windows")
    t = sub_once(t, "static void draw_devices(struct list_head *devices, struct nvtop_interface *interface) {",
                 meter_block + "\n\nstatic void draw_devices(struct list_head *devices, struct nvtop_interface *interface) {",
                 "meter block insertion")
    t = sub_once(t, draw_anchor, draw_new, "meter draw call")
    t = sub_once(t, "static void initialize_all_windows(struct nvtop_interface *dwin) {",
                 layout_helper + "\n\n" + plot_area_fn + "\n\nstatic void initialize_all_windows(struct nvtop_interface *dwin) {",
                 "layout helpers")
    t = sub_once(t, layout_anchor, layout_new, "layout reservation")
    t = sub_once(t, poll_anchor, poll_new, "poll on redraw")
    open(p, "w").write(t)

    # ---- src/nvtop.c -------------------------------------------------------
    p = os.path.join(target, "src/nvtop.c")
    t = open(p).read()
    t = sub_once(t, '#include "nvtop/interface_options.h"\n',
                 '#include "nvtop/interface_options.h"\n#include "nvtop/model_meter_c.h"\n', "nvtop.c include")
    t = sub_once(t,
                 "      initialize_curses(allDevCount, numMonitoredGpus, interface_largest_gpu_name(&monitoredGpus), "
                 "allDevicesOptions);\n  timeout(interface_update_interval(interface));\n",
                 "      initialize_curses(allDevCount, numMonitoredGpus, interface_largest_gpu_name(&monitoredGpus), "
                 "allDevicesOptions);\n  timeout(interface_update_interval(interface));\n\n"
                 "  /* First LLM model meter poll */\n  nvtop_model_meter_poll();\n", "nvtop.c first poll")
    t = sub_once(t, "  clean_ncurses(interface);",
                 "  /* Shutdown LLM model meter */\n  nvtop_model_meter_shutdown();\n\n  clean_ncurses(interface);",
                 "nvtop.c shutdown")
    open(p, "w").write(t)

    # ---- src/CMakeLists.txt -----------------------------------------------
    p = os.path.join(target, "src/CMakeLists.txt")
    t = open(p).read()
    t = sub_once(t, "  ini.c\n)", "  ini.c\n  llm_poller.c\n)", "cmake source list")
    t = sub_once(t, "target_link_libraries(nvtop\n  PRIVATE ncurses m ${CMAKE_DL_LIBS})",
                 "find_package(CURL)\nif(CURL_FOUND)\n  target_link_libraries(nvtop PRIVATE ${CURL_LIBRARIES})\n"
                 "  target_include_directories(nvtop PRIVATE ${CURL_INCLUDE_DIRS})\nendif()\n\n"
                 "target_link_libraries(nvtop\n  PRIVATE ncurses m ${CMAKE_DL_LIBS})", "cmake curl")
    open(p, "w").write(t)

    gap_fix(target, variant)
    print(f"ported OK: {target}  (variant: {variant})")


if __name__ == "__main__":
    main()
