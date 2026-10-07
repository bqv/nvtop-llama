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

    print(f"ported OK: {target}  (variant: {variant})")


if __name__ == "__main__":
    main()
