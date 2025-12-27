import re

path = r"c:/Users/nabar/OneDrive/Documents/Fusion 360/NC Programs/1001.nc"  # <- change this

xmin = ymin = float("inf")
xmax = ymax = float("-inf")

x = y = None

with open(path, "r", encoding="utf-8", errors="ignore") as f:
    for line in f:
        # ignore comments
        line = line.split("(")[0]
        mx = re.search(r"[ \t]X(-?\d+(\.\d+)?)", line)
        my = re.search(r"[ \t]Y(-?\d+(\.\d+)?)", line)
        if mx:
            x = float(mx.group(1))
        if my:
            y = float(my.group(1))
        if x is not None and y is not None and (mx or my):
            xmin = min(xmin, x); xmax = max(xmax, x)
            ymin = min(ymin, y); ymax = max(ymax, y)

print("X:", xmin, "to", xmax, "span", xmax - xmin)
print("Y:", ymin, "to", ymax, "span", ymax - ymin)
