#!/usr/bin/env bash
# Build docs/WattNot-Design.pdf from docs/WattNot-Design.md.
#   1. render the architecture diagram SVG -> PNG (cairosvg)
#   2. markdown -> HTML with simple clean CSS (python-markdown)
#   3. HTML -> PDF (headless Chrome)
# Re-runnable. Needs: python3 + markdown + cairosvg, google-chrome (or chromium).
set -euo pipefail
cd "$(dirname "$0")/.."

SRC=docs/WattNot-Design.md
SVG=docs/img/wattnot-architecture.svg
PNG=docs/img/wattnot-architecture.png
HTML=docs/WattNot-Design.html      # kept next to img/ so the relative image path resolves
PDF=docs/WattNot-Design.pdf

echo "1/3 diagram: $SVG -> $PNG"
python3 -c "import cairosvg; cairosvg.svg2png(url='$SVG', write_to='$PNG', output_width=1360, output_height=1000)"

echo "2/3 markdown -> html"
python3 - "$SRC" "$HTML" <<'PY'
import sys, markdown
src, out = sys.argv[1], sys.argv[2]
body = markdown.markdown(
    open(src, encoding="utf-8").read(),
    extensions=["tables", "fenced_code", "toc", "attr_list", "sane_lists"],
)
css = """
@page { size: A4; margin: 18mm 16mm; }
* { box-sizing: border-box; }
body { font-family: -apple-system, Segoe UI, Helvetica, Arial, sans-serif; color: #1b2733;
       font-size: 11pt; line-height: 1.5; max-width: 820px; margin: 0 auto; }
h1 { font-size: 22pt; border-bottom: 2px solid #1b2733; padding-bottom: 6px; }
h2 { font-size: 15pt; margin-top: 1.6em; border-bottom: 1px solid #d3dae2; padding-bottom: 3px; }
h3 { font-size: 12.5pt; margin-top: 1.2em; }
p, li { color: #26323d; }
a { color: #2d6cdf; text-decoration: none; }
code { font-family: JetBrains Mono, Consolas, monospace; font-size: 9.5pt;
       background: #f1f4f7; padding: 1px 4px; border-radius: 3px; }
pre { background: #f4f6f8; border: 1px solid #d3dae2; border-radius: 6px; padding: 10px 12px;
      overflow-x: auto; }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; width: 100%; margin: 0.8em 0; font-size: 9.5pt; }
th, td { border: 1px solid #c3ccd6; padding: 5px 8px; text-align: left; vertical-align: top; }
th { background: #eef2f6; }
img { max-width: 100%; height: auto; border: 1px solid #d3dae2; border-radius: 6px; }
hr { border: none; border-top: 1px solid #d3dae2; margin: 1.6em 0; }
"""
html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>WattNot — Design</title><style>{css}</style></head><body>{body}</body></html>"""
open(out, "w", encoding="utf-8").write(html)
print("wrote", out)
PY

echo "3/3 html -> pdf"
CHROME="$(command -v google-chrome || command -v chromium || command -v chromium-browser)"
"$CHROME" --headless --no-sandbox --disable-gpu --no-pdf-header-footer \
  --print-to-pdf="$PDF" "file://$(pwd)/$HTML" >/dev/null 2>&1
rm -f "$HTML"
echo "done: $PDF"
ls -la "$PDF"
