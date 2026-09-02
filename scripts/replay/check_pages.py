"""Scan every rendered page for anything a merchant should never read."""
import glob, json, pathlib, re, sys

PATTERNS = {
    "actor id":        r"\bm_[a-z0-9_]+",
    "correlation id":  r"\bturn_\d+_[a-z0-9_]+|\bshop_[a-z0-9]+",
    "settlement id":   r"\bstl_[0-9a-f]+",
    "order/match id":  r"\b(ord|mch|dec|ins|evt)_[0-9a-f]{6,}",
    "the word paise":  r"\bpaise\b",
    "bare big number": r"(?<![\d,.₹])\b\d{5,}\b(?!\s*(units|events))",
    # A COMMA BREAKS A RUN OF DIGITS, and both this scanner and the rewriter
    # keyed on the run. "(12,000 vs 24,500)" in an agent's own sentence is
    # ₹120 against ₹245 and it read straight through every check.
    "money without a ₹": r"(?<![₹\d,.])\b\d{1,3}(?:,\d{3})+\b"
                         r"(?!\s*(?:units|events|posts|threads|merchants|"
                         r"trades|points))",
}

def visible(html):
    s = re.sub(r"<style>.*?</style>", " ", html, flags=re.S)
    s = re.sub(r"<script[^>]*>.*?</script>", " ", s, flags=re.S)
    s = re.sub(r'\s(data-[a-z]+|href|id|class|aria-label)="[^"]*"', " ", s)
    return re.sub(r"<[^>]+>", "\n", s)

# The keys whose values a reader actually sees. Everything else in the JSON
# is identity the page joins on, and an id is meant to look like an id there.
READABLE = ("head", "lines", "says", "text", "title", "why", "said", "label",
            "need", "detail", "reason")


def payloads(html):
    """Text the page renders from JSON at runtime — just as visible."""
    out = []
    for m in re.finditer(r'<script type="application/json"[^>]*>(.*?)</script>',
                         html, re.S):
        try:
            data = json.loads(m.group(1))
        except Exception:
            continue
        # DESCEND EVERYTHING, COLLECT ONLY WHAT A READER SEES. Gating the
        # recursion on the key name instead of the collection stopped the
        # walk at the root — whose keys are "rails" and "crew", not "said" —
        # so this scanned nothing at all and reported every page clean while
        # actor ids and paise sat in the JSON the page renders from.
        def walk(node, shown=False):
            if isinstance(node, str):
                if shown:
                    out.append(node)
            elif isinstance(node, dict):
                for k, v in node.items():
                    walk(v, shown or k in READABLE)
            elif isinstance(node, list):
                for v in node:
                    walk(v, shown)
        walk(data)
    return "\n".join(out)

# product.html is the written submission, not a merchant's dashboard: naming
# a settlement id and using "paise" is correct in a technical document.
# "18650" is a lithium cell size, part of a product name, not money.
SKIP_FILES = {"docs/product.html"}
ALLOW = {"18650"}
# desk.html is the processor's own console, behind a passcode. The accountant
# freezing a merchant says WHICH settlement disagreed, and to whoever acts on
# that the id is the useful half of the sentence. Everything else on the desk
# is still held to the merchant standard — this is one pattern on one page,
# not a page waved through.
EXCEPT = {("docs/desk.html", "settlement id")}


def scan(paths):
    """Every page, every pattern. {pattern: {page: hits}}."""
    found = {}
    for f in sorted(paths):
        if f in SKIP_FILES:
            continue
        html = pathlib.Path(f).read_text()
        text = visible(html) + "\n" + payloads(html)
        for name, pat in PATTERNS.items():
            hits = sorted({m.group(0) for m in re.finditer(pat, text)} - ALLOW)
            if hits and (f, name) not in EXCEPT:
                found.setdefault(name, {})[f] = hits[:4]
    return found


def main() -> int:
    found = scan(glob.glob("docs/*.html"))
    if not found:
        print("CLEAN — nothing a merchant should not read")
        return 0
    for name, files in found.items():
        n = len(files)
        print(f"\n{name}  ({n} page{'s' if n != 1 else ''})")
        for f, hits in list(files.items())[:2]:
            print(f"   {f.split('/')[-1]:<26} {hits}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
