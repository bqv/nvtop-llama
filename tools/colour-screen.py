#!/usr/bin/env python3
"""Reconstruct a screen from an ANSI stream, keeping the colour of every cell.

usage: colour-screen.py FILE ROWS COLS [--mask FG]

Without --mask it lists the foreground colours present, one per line, as
`fg=N cells=M`; with --mask FG it prints the grid, '#' where a cell is drawn in
that foreground colour.
"""
import re
import sys

SGR = re.compile(r"\x1b\[([0-9;?]*)([a-zA-Z])")


def parse(path, rows, cols):
    data = open(path, "rb").read().decode("utf-8", "replace")
    screen = [[" "] * cols for _ in range(rows)]
    colour = [[-1] * cols for _ in range(rows)]
    r = c = 0
    fg = -1
    i = 0
    n = len(data)
    while i < n:
        ch = data[i]
        if ch == "\x1b":
            m = SGR.match(data[i:])
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
                        colour = [[-1] * cols for _ in range(rows)]
                elif cmd == "K":
                    for x in range(max(c, 0), cols):
                        screen[min(max(r, 0), rows - 1)][x] = " "
                        colour[min(max(r, 0), rows - 1)][x] = -1
                elif cmd == "m":
                    j = 0
                    while j < len(nums):
                        v = nums[j]
                        if v == 0 or v == 39:
                            fg = -1
                        elif 30 <= v <= 37:
                            fg = v - 30
                        elif 90 <= v <= 97:
                            fg = v - 90 + 8
                        elif v == 38 and j + 2 < len(nums) and nums[j + 1] == 5:
                            fg = nums[j + 2]
                            j += 2
                        j += 1
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
                colour[r][c] = fg
            c += 1
        i += 1
    return screen, colour


def legend_colour(screen, colour, text):
    """Foreground colour of the cell holding the first character of `text`."""
    for r, row in enumerate(screen):
        line = "".join(row)
        col = line.find(text)
        if col >= 0:
            return colour[r][col], r, col
    return None, None, None


if __name__ == "__main__":
    path = sys.argv[1]
    rows, cols = int(sys.argv[2]), int(sys.argv[3])
    screen, colour = parse(path, rows, cols)
    if len(sys.argv) > 4 and sys.argv[4] == "--mask":
        want = int(sys.argv[5])
        for r, row in enumerate(screen):
            line = "".join("#" if colour[r][x] == want and row[x] != " " else "." for x in range(cols))
            print(f"{r:>3}|{line}")
    elif len(sys.argv) > 4 and sys.argv[4] == "--legend":
        c, r, col = legend_colour(screen, colour, sys.argv[5])
        print(f"legend {sys.argv[5]!r} at row {r} col {col} fg={c}")
    else:
        seen = {}
        for r in range(rows):
            for x in range(cols):
                if screen[r][x] != " " and colour[r][x] >= 0:
                    seen[colour[r][x]] = seen.get(colour[r][x], 0) + 1
        for fg in sorted(seen):
            print(f"fg={fg} cells={seen[fg]}")
