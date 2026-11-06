#!/usr/bin/env python3
"""Gold-only report: price, structure, levels, scenarios and news, in Arabic.

    python3 scripts/gold_report.py                 # reports/YYYY-MM-DD.md and reports/latest.md
    python3 scripts/gold_report.py --stdout        # print instead of writing
    python3 scripts/gold_report.py --fixtures tests/fixtures/trader-gold   # offline demo

Reads public data only (Yahoo daily candles for the COMEX gold future GC=F and
Google News headlines). Nothing here places an order: the scenarios are study
notes with levels, not advice. A failing source becomes a line in the report.
Standard library only. Settings: trader/gold.json (see trader/gold.example.json).
"""
import argparse
import datetime as dt
import email.utils
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = "trader-skill-gold-report"
SYMBOL = "GC=F"

DEFAULTS = {
    "level_step": 100,        # round-number levels that raise an alert when crossed
    "lookback_days": 60,      # window for the value area and swing levels
    "value_area_pct": 70,
    "atr_stop_mult": 1.5,
    "swing_bars": 3,          # bars on each side of a swing high or low
    "news_max": 12,
    "near_atr": 0.3,          # within this many ATRs of support/resistance raises a "near level" alert
    "news_hours": 48,
    # Local jewellery and retail price stories are not about trading gold.
    "news_exclude": ["عيار", "جنيه", "جنيها", "جنيهات", "الجرام", "جرام", "دينار", "ريال", "درهم", "بكام"],
}

# Several searches per language, merged and de-duplicated, so one narrow query cannot starve the report.
NEWS_QUERIES = {
    "ar": ["الذهب الأوقية دولار when:2d", "الذهب الفيدرالي الدولار عوائد when:2d", "الذهب عالميا ارتفع انخفض when:2d"],
    "en": ["gold price XAUUSD when:2d", "gold Fed dollar yields when:2d", "gold futures COMEX when:2d"],
}


def news_url(key, query):
    if key == "ar":
        return "https://news.google.com/rss/search?q=%s&hl=ar&gl=DZ&ceid=DZ:ar" % urllib.parse.quote(query)
    return "https://news.google.com/rss/search?q=%s&hl=en-US&gl=US&ceid=US:en" % urllib.parse.quote(query)


DISCLAIMER = (
    "> للدراسة والمعلومة فقط وليس نصيحة مالية ولا توصية بالشراء أو البيع. السعر هو عقد الذهب الآجل GC=F "
    "(قد يختلف قليلاً عن السعر الفوري XAUUSD) والبيانات يومية ومتأخرة. المستويات تقديرات حسابية وقد تخطئ. "
    "لا يُنفَّذ أي أمر من هذا المشروع."
)


# ------------------------------------------------------------------ fetching

def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(3_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise RuntimeError("%s أجاب HTTP %d" % (url.split("/")[2], exc.code))
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError("تعذّر الوصول إلى %s: %s" % (url.split("/")[2], getattr(exc, "reason", exc)))


def read_source(fixtures, name, url):
    if fixtures:
        path = os.path.join(fixtures, name)
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read()
        except OSError as exc:
            raise RuntimeError("ملف الاختبار غير موجود: %s" % exc.filename)
    return fetch(url)


def parse_chart(text):
    """Yahoo chart JSON -> (meta, candles). Candles with missing fields are dropped."""
    try:
        res = json.loads(text)["chart"]["result"][0]
        stamps = res["timestamp"]
        q = res["indicators"]["quote"][0]
        meta = res["meta"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise RuntimeError("Yahoo أعاد شكلاً غير متوقع")
    candles = []
    for i, ts in enumerate(stamps):
        try:
            o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        except (IndexError, KeyError):
            continue
        if None in (o, h, l, c) or h < l:
            continue
        v = (q.get("volume") or [None] * len(stamps))[i] or 0
        candles.append({"t": ts, "o": float(o), "h": float(h), "l": float(l), "c": float(c), "v": float(v)})
    if len(candles) < 30:
        raise RuntimeError("شموع قليلة جداً (%d) للتحليل" % len(candles))
    return meta, candles


def parse_news(text, limit_hours, now, exclude=()):
    """Google News RSS -> [{title, source, link, time}] newest first."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise RuntimeError("خلاصة الأخبار ليست XML صالحاً")
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        src = (it.findtext("source") or "").strip()
        when = None
        try:
            when = email.utils.parsedate_to_datetime(it.findtext("pubDate") or "")
            if when.tzinfo is None:
                when = when.replace(tzinfo=dt.timezone.utc)
        except (TypeError, ValueError):
            pass
        if not title or not link.startswith("http"):
            continue
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        if when and (now - when).total_seconds() > limit_hours * 3600:
            continue
        if any(word in title for word in exclude):
            continue
        items.append({"title": title, "source": src, "link": link, "time": when})
    items.sort(key=lambda i: i["time"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
    return items


# ------------------------------------------------------------------ analysis

def ema(values, n):
    k = 2.0 / (n + 1)
    out = values[0]
    for v in values[1:]:
        out = v * k + out * (1 - k)
    return out


def atr(candles, n=14):
    trs = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        trs.append(max(c["h"] - c["l"], abs(c["h"] - p["c"]), abs(c["l"] - p["c"])))
    trs = trs[-n:]
    return sum(trs) / len(trs) if trs else 0.0


def swings(candles, k):
    """Fractal swing highs and lows: [(index, price)], oldest first."""
    highs, lows = [], []
    for i in range(k, len(candles) - k):
        win = candles[i - k: i + k + 1]
        if candles[i]["h"] == max(c["h"] for c in win):
            highs.append((i, candles[i]["h"]))
        if candles[i]["l"] == min(c["l"] for c in win):
            lows.append((i, candles[i]["l"]))
    return highs, lows


def efficiency(candles, n=20):
    seg = [c["c"] for c in candles[-(n + 1):]]
    path = sum(abs(seg[i] - seg[i - 1]) for i in range(1, len(seg)))
    return abs(seg[-1] - seg[0]) / path if path else 0.0


def value_area(candles, pct, bins=40):
    """Approximate volume profile from daily bars: (poc, vah, val) or None without volume."""
    lo, hi = min(c["l"] for c in candles), max(c["h"] for c in candles)
    total = sum(c["v"] for c in candles)
    if hi <= lo or total <= 0:
        return None
    size = (hi - lo) / bins
    prof = [0.0] * bins
    for c in candles:
        a = min(bins - 1, int((c["l"] - lo) / size))
        b = min(bins - 1, int((c["h"] - lo) / size))
        share = c["v"] / (b - a + 1)
        for i in range(a, b + 1):
            prof[i] += share
    poc = max(range(bins), key=lambda i: prof[i])
    left = right = poc
    got = prof[poc]
    while got < total * pct / 100.0 and (left > 0 or right < bins - 1):
        up = prof[right + 1] if right < bins - 1 else -1
        dn = prof[left - 1] if left > 0 else -1
        if up >= dn:
            right += 1
            got += prof[right]
        else:
            left -= 1
            got += prof[left]
    return lo + (poc + 0.5) * size, lo + (right + 1) * size, lo + left * size


def analyse(candles, price, cfg):
    look = candles[-int(cfg["lookback_days"]):]
    closes = [c["c"] for c in candles]
    a = atr(candles)
    e20, e50 = ema(closes, 20), ema(closes, 50)
    highs, lows = swings(candles, int(cfg["swing_bars"]))
    highs = [s for s in highs if s[0] >= len(candles) - int(cfg["lookback_days"])]
    lows = [s for s in lows if s[0] >= len(candles) - int(cfg["lookback_days"])]
    if e20 > e50 and price > e50:
        trend = "صاعد"
    elif e20 < e50 and price < e50:
        trend = "هابط"
    else:
        trend = "مختلط"
    structure = "متذبذب"
    if len(highs) >= 2 and len(lows) >= 2:
        if highs[-1][1] > highs[-2][1] and lows[-1][1] > lows[-2][1]:
            structure = "قمم وقيعان أعلى (صاعد)"
        elif highs[-1][1] < highs[-2][1] and lows[-1][1] < lows[-2][1]:
            structure = "قمم وقيعان أدنى (هابط)"
    resist = sorted(h for _, h in highs if h > price)
    support = sorted((l for _, l in lows if l < price), reverse=True)
    r = resist[0] if resist else price + 2 * a
    s = support[0] if support else price - 2 * a
    va = value_area(look, float(cfg["value_area_pct"]))
    zone, mid_value = "غير متاح (لا حجم)", False
    if va:
        poc, vah, val = va
        if price > vah:
            zone = "فوق منطقة القيمة"
        elif price < val:
            zone = "تحت منطقة القيمة"
        else:
            third = (vah - val) / 3.0
            mid_value = val + third <= price <= vah - third
            zone = "منتصف منطقة القيمة" if mid_value else "داخل منطقة القيمة (قرب الحافة)"
    score = (1 if trend == "صاعد" else -1 if trend == "هابط" else 0)
    score += (1 if "صاعد" in structure else -1 if "هابط" in structure else 0)
    if va:
        score += 1 if price > va[0] else -1
    er = efficiency(candles)
    # A choppy range is not a trend: consolidation near an edge is not an automatic buy or sell.
    verdict = ("مائل للصعود" if score >= 2 else "مائل للهبوط" if score <= -2 else "حياد / انتظار") if er >= 0.25 \
        else "حياد / انتظار"
    return {"atr": a, "ema20": e20, "ema50": e50, "trend": trend, "structure": structure, "er": er,
            "va": va, "zone": zone, "mid_value": mid_value, "resist": r, "support": s, "verdict": verdict,
            "hi": max(c["h"] for c in look), "lo": min(c["l"] for c in look)}


def merge_news(lists):
    """Several feeds -> one list, newest first, without repeated links or headlines."""
    seen, out = set(), []
    for items in lists:
        for it in items:
            key = (it["link"], " ".join(it["title"].split()).lower())
            if key[0] in seen or key[1] in seen:
                continue
            seen.update(key)
            out.append(it)
    out.sort(key=lambda i: i["time"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
    return out


def scenarios(price, an, cfg):
    """Two conditional study scenarios. Entry is never from the middle of value."""
    stop_d = an["atr"] * float(cfg["atr_stop_mult"])
    r, s = an["resist"], an["support"]
    rows = []
    long_stop = r - stop_d
    rows.append(("صعودي: كسر المقاومة", "إغلاق يومي فوق %.1f ثم إعادة اختبار تثبت فوقه" % r,
                 long_stop, r + 2 * (r - long_stop)))
    short_stop = s + stop_d
    rows.append(("هبوطي: كسر الدعم", "إغلاق يومي تحت %.1f ثم إعادة اختبار يفشل في العودة" % s,
                 short_stop, s - 2 * (short_stop - s)))
    return rows


def crossed(prev, cur, step):
    """Round levels between two prices, in the order they were crossed."""
    if prev is None or step <= 0 or prev == cur:
        return []
    lo, hi = sorted((prev, cur))
    n = int(lo // step) + 1
    out = []
    while n * step <= hi:
        out.append(n * step)
        n += 1
    return out if cur > prev else out[::-1]


# ------------------------------------------------------------------ report

def cell(text):
    return str(text if text is not None else "").replace("|", "/").replace("\n", " ").strip()


def table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    return out + ["| " + " | ".join(cell(c) for c in row) + " |" for row in rows]


def news_lines(items, n):
    lines = []
    for it in items[:n]:
        when = it["time"].strftime("%m-%d %H:%M UTC") if it["time"] else ""
        lines.append("- [%s](%s) · %s %s" % (cell(it["title"]), it["link"], cell(it["source"]), when))
    return lines


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else default
    except (OSError, ValueError):
        return default


def build(cfg, fixtures, today, state):
    errors, body, summary = [], [], []
    now = dt.datetime.now(dt.timezone.utc)
    if fixtures:
        now = dt.datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc, hour=23)
    price = prev_close = None
    an = None
    try:
        meta, candles = parse_chart(read_source(
            fixtures, "chart-GC=F.json",
            "https://query1.finance.yahoo.com/v8/finance/chart/%s?range=1y&interval=1d" % urllib.parse.quote(SYMBOL)))
        price = float(meta.get("regularMarketPrice") or candles[-1]["c"])
        prev_close = meta.get("chartPreviousClose") or meta.get("previousClose") or candles[-2]["c"]
        an = analyse(candles, price, cfg)
    except RuntimeError as exc:
        errors.append("السعر والتحليل: %s" % exc)

    if an:
        chg = (price / float(prev_close) - 1) * 100 if prev_close else 0.0
        summary.append("- **السعر:** %s$ (%+.2f%% عن الإغلاق السابق)" % ("{:,.1f}".format(price), chg))
        summary.append("- **الحكم الحالي:** %s. الاتجاه %s، والبنية: %s." % (an["verdict"], an["trend"], an["structure"]))
        summary.append("- **موقع السعر:** %s." % an["zone"])
        summary.append("- **أقرب دعم / مقاومة:** %.1f$ / %.1f$." % (an["support"], an["resist"]))
        notify = []
        for name, cond, stop, target in scenarios(price, an, cfg):
            side = "صعودي" if name.startswith("صعودي") else "هبوطي"
            summary.append("- **سيناريو %s:** %s؛ وقف الخسارة %.1f$؛ هدف 1:2 عند %.1f$." % (side, cond, stop, target))
        if an["mid_value"]:
            summary.append("- **تنبيه:** السعر في منتصف القيمة، ولا دخول من هناك. الأفضل الانتظار.")
        prev = state.get("last_price")
        for lvl in crossed(prev, price, float(cfg["level_step"])):
            notify.append("level")
            summary.append("- **تنبيه مستوى:** عبر الذهب %s$ %s." % ("{:,.0f}".format(lvl),
                                                                  "صعوداً" if price > (prev or 0) else "هبوطاً"))
        near = float(cfg["near_atr"]) * an["atr"]
        for label, lvl in (("المقاومة", an["resist"]), ("الدعم", an["support"])):
            if abs(price - lvl) <= near:
                notify.append("near")
                summary.append("- **تنبيه اقتراب:** السعر قرب %s %.1f$. راقب الإغلاق اليومي ثم إعادة الاختبار، "
                               "ولا دخول قبلهما." % (label, lvl))
        if state.get("last_verdict") not in (None, an["verdict"]):
            notify.append("verdict")
            summary.append("- **تغيّر الحكم:** من %s إلى %s." % (state["last_verdict"], an["verdict"]))
        state["last_verdict"] = an["verdict"]
        state["notify"] = sorted(set(notify))
        state["last_price"] = price
        state["last_time"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    news = {"ar": [], "en": []}
    for key, queries in NEWS_QUERIES.items():
        got, failed = [], []
        for i, query in enumerate(queries):
            if fixtures and i:
                continue  # saved test data has one file per language
            name = "news-%s.xml" % key
            try:
                got.append(parse_news(read_source(fixtures, name, news_url(key, query)),
                                      float(cfg["news_hours"]), now, cfg.get("news_exclude") or ()))
            except RuntimeError as exc:
                failed.append(str(exc))
        news[key] = merge_news(got)
        if failed and not got:
            errors.append("أخبار (%s): %s" % ("عربي" if key == "ar" else "إنجليزي", failed[0]))
    latest = merge_news([news["ar"][:3], news["en"][:3]])[:3]
    for n, top in enumerate(latest):
        summary.append("- **%s:** [%s](%s)" % ("أحدث خبر" if n == 0 else "خبر", cell(top["title"]), top["link"]))

    if an:
        va = an["va"]
        body += ["## التحليل", ""]
        body += table(["المؤشر", "القيمة"], [
            ("الاتجاه (EMA 20 و 50)", "%s (%.1f / %.1f)" % (an["trend"], an["ema20"], an["ema50"])),
            ("بنية السوق", an["structure"]),
            ("كفاءة الحركة (20 يوم)", "%.2f (قريب من 1 = حركة اتجاهية، قريب من 0 = تذبذب)" % an["er"]),
            ("المدى اليومي المتوسط (ATR 14)", "%.1f$" % an["atr"]),
            ("أعلى / أدنى سعر خلال %d يوماً" % int(cfg["lookback_days"]), "%.1f$ / %.1f$" % (an["hi"], an["lo"])),
            ("مركز الحجم POC", ("%.1f$" % va[0]) if va else "غير متاح"),
            ("منطقة القيمة VAL إلى VAH", ("%.1f$ إلى %.1f$" % (va[2], va[1])) if va else "غير متاح"),
        ])
        body += ["", "## سيناريوهات للدراسة", "",
                 "الدخول لا يكون من منتصف القيمة، والكسر يحتاج إغلاقاً ثم إعادة اختبار. الأرقام مبنية على ATR.", ""]
        body += table(["السيناريو", "الشرط", "وقف الخسارة", "هدف 1:2"],
                      [(n, c, "%.1f$" % s, "%.1f$" % t) for n, c, s, t in scenarios(price, an, cfg)])
        body.append("")
    for key, title in (("ar", "أخبار الذهب بالعربية"), ("en", "أخبار الذهب بالإنجليزية")):
        body += ["## " + title, ""]
        lines = news_lines(news[key], int(cfg["news_max"]))
        body += lines if lines else ["لا أخبار جديدة في آخر %d ساعة." % int(cfg["news_hours"])]
        body.append("")

    head = ["# تقرير الذهب - %s" % today, ""]
    head += (["## الملخص", ""] + summary + [""]) if summary else []
    tail = []
    if errors:
        tail += ["## ملاحظات وأخطاء", ""] + ["- " + cell(e) for e in errors] + [""]
    tail += ["---", DISCLAIMER, ""]
    return "\n".join(head + body + tail), errors


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", default=os.path.join(ROOT, "trader", "gold.json"))
    ap.add_argument("--state", default=os.path.join(ROOT, "trader", "state", "gold.json"))
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "reports"))
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default: today, UTC)")
    ap.add_argument("--fixtures", default=None, help="read saved test data instead of the network")
    ap.add_argument("--stdout", action="store_true", help="print the report instead of writing files")
    args = ap.parse_args(argv)

    today = args.date or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in load_json(args.config, {}).items() if k in DEFAULTS})
    state = load_json(args.state, {})
    text, errors = build(cfg, os.path.abspath(args.fixtures) if args.fixtures else None, today, state)
    if args.stdout:
        sys.stdout.write(text)
        return 0
    os.makedirs(args.out_dir, exist_ok=True)
    for name in (today + ".md", "latest.md"):
        path = os.path.join(args.out_dir, name)
        with open(path + ".tmp", "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(path + ".tmp", path)
    if not args.fixtures:
        os.makedirs(os.path.dirname(args.state), exist_ok=True)
        with open(args.state + ".tmp", "w", encoding="utf-8", newline="\n") as fh:
            json.dump(state, fh, indent=2)
        os.replace(args.state + ".tmp", args.state)
    print("wrote reports/%s.md (%d problems)" % (today, len(errors)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
