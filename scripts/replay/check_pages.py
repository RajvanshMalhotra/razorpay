"""Scan every rendered page for anything a merchant should never read."""
import glob, json, pathlib, re, sys

PATTERNS = {
    "actor id":        r"\bm_[a-z0-9_]+",
    "correlation id":  r"\bturn_\d+_[a-z0-9_]+|\bshop_[a-z0-9]+",
    "settlement id":   r"\bstl_[0-9a-f]+",
    "order/match id":  r"\b(ord|mch|dec|ins|evt)_[0-9a-f]{6,}",
    "the word paise":  r"\bpaise\b",
    "bare big number": r"(?<![\d,.₹])\b\d{5,}\b(?!\s*(units|events))",
}

def visible(html):
    s = re.sub(r"<style>.*?</style>", " ", html, flags=re.S)
    s = re.sub(r"<script[^>]*>.*?</script>", " ", s, flags=re.S)
    s = re.sub(r'\s(data-[a-z]+|href|id|class|aria-label)="[^"]*"', " ", s)
    return re.sub(r"<[^>]+>", "\n", s)

def payloads(html):
    """Text the page renders from JSON at runtime — just as visible."""
    out = []
    for m in re.finditer(r'<script type="application/json"[^>]*>(.*?)</script>',
                         html, re.S):
        try:
            data = json.loads(m.group(1))
        except Exception:
            continue
        def walk(node):
            if isinstance(node, str): out.append(node)
            elif isinstance(node, dict):
                for k, v in node.items():
                    if k in ("head", "lines", "says", "text", "title", "why",
                             "said", "label", "need", "detail", "reason"):
                        walk(v)
            elif isinstance(node, list):
                for v in node: walk(v)
        walk(data)
    return "\n".join(out)

found = {}
# product.html is the written submission, not a merchant's dashboard: naming
# a settlement id and using "paise" is correct in a technical document.
# "18650" is a lithium cell size, part of a product name, not money.
SKIP_FILES = {"docs/product.html"}
ALLOW = {"18650"}

for f in sorted(glob.glob("docs/*.html")):
    if f in SKIP_FILES:
        continue
    html = pathlib.Path(f).read_text()
    text = visible(html) + "\n" + payloads(html)
    for name, pat in PATTERNS.items():
        hits = sorted({m.group(0) for m in re.finditer(pat, text)} - ALLOW)
        if hits:
            found.setdefault(name, {})[f] = hits[:4]

if not found:
    print("CLEAN — nothing a merchant should not read")
    sys.exit(0)
for name, files in found.items():
    n = len(files)
    sample = list(files.items())[:2]
    print(f"\n{name}  ({n} page{'s' if n != 1 else ''})")
    for f, hits in sample:
        print(f"   {f.split('/')[-1]:<26} {hits}")
sys.exit(1)
