#!/usr/bin/env python3
"""Reconstruct the final screen from a `script` typescript (minimal ANSI/CSI)."""
import re
import sys


def render(path, rows=50, cols=200):
    data = open(path, "rb").read().decode("utf-8", "replace")
    # Drop the script(1) header/footer lines not written by the program.
    screen = [[" "] * cols for _ in range(rows)]
    r = c = 0
    i = 0
    n = len(data)
    while i < n:
        ch = data[i]
        if ch == "\x1b":
            m = re.match(r"\x1b\[([0-9;?]*)([a-zA-Z])", data[i:])
            if m:
                params, cmd = m.group(1), m.group(2)
                nums = [int(x) for x in params.split(";") if x.isdigit()]
                if cmd == "H" or cmd == "f":
                    r = (nums[0] - 1) if len(nums) > 0 else 0
                    c = (nums[1] - 1) if len(nums) > 1 else 0
                elif cmd == "A":
                    r -= nums[0] if nums else 1
                elif cmd == "B":
                    r += nums[0] if nums else 1
                elif cmd == "C":
                    c += nums[0] if nums else 1
                elif cmd == "D":
                    c -= nums[0] if nums else 1
                elif cmd == "G":
                    c = (nums[0] - 1) if nums else 0
                elif cmd == "d":
                    r = (nums[0] - 1) if nums else 0
                elif cmd == "J":
                    if (nums[0] if nums else 0) == 2:
                        screen = [[" "] * cols for _ in range(rows)]
                elif cmd == "K":
                    for x in range(max(c, 0), cols):
                        screen[min(max(r, 0), rows - 1)][x] = " "
                i += m.end()
                continue
            m2 = re.match(r"\x1b[()][A-Z0-9]", data[i:])
            if m2:
                i += m2.end()
                continue
            i += 1
            continue
        if ch == "\r":
            c = 0
        elif ch == "\n":
            r += 1
        elif ch == "\b":
            c = max(0, c - 1)
        elif ch >= " ":
            if 0 <= r < rows and 0 <= c < cols:
                screen[r][c] = ch
            c += 1
        i += 1
    return ["".join(row).rstrip() for row in screen]


if __name__ == "__main__":
    path = sys.argv[1]
    rows = int(sys.argv[2]) if len(sys.argv) > 2 else 50
    cols = int(sys.argv[3]) if len(sys.argv) > 3 else 200
    for idx, line in enumerate(render(path, rows, cols)):
        print(f"{idx:>3}|{line}")
