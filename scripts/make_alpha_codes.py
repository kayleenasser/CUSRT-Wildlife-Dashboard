"""Turn the IBP alpha-code PDF into scripts/alpha_codes.csv (code, english_name).

Run once whenever a new Alpha_codes_eng.pdf comes out:
    python scripts/make_alpha_codes.py Alpha_codes_eng.pdf

Needs the `pdftotext` command (poppler; ships with Git for Windows).
"""

import csv
import re
import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "alpha_codes.csv"

# "+ Snow/Ross's Goose SRGO Anser caerulescens/rossii ANSCRO" -> name, code
LINE = re.compile(r"^(?:\+ )?(.+?) ([A-Z]{4})\*? (.+) ([A-Z]{6})\*?$")


def main():
    pdf = sys.argv[1] if len(sys.argv) > 1 else "Alpha_codes_eng.pdf"
    text = subprocess.run(["pdftotext", "-raw", "-enc", "UTF-8", pdf, "-"],
                          capture_output=True, check=True).stdout.decode("utf-8")
    codes = {}
    for line in text.splitlines():
        m = LINE.match(line.strip())
        if m and m[2] not in codes:
            codes[m[2]] = m[1].replace("’", "'")
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["code", "english_name"])
        w.writerows(sorted(codes.items()))
    print(f"Wrote {len(codes)} codes to {OUT}")


if __name__ == "__main__":
    main()
