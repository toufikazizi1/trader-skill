#!/usr/bin/env python3
"""Daily report: runs the trader-* tools and writes one Arabic markdown file.

    python3 scripts/daily_report.py                  # reports/YYYY-MM-DD.md
    python3 scripts/daily_report.py --stdout         # print instead of writing
    python3 scripts/daily_report.py --fixtures tests/fixtures   # offline demo

Nothing here places an order. It only reads public data (SEC EDGAR, the House
Clerk, news feeds, delayed quotes) and writes a report. Every tool runs as its
own process and a failure in one becomes a line in the report, never a crash.

Settings live in trader/report.json (see trader/report.example.json). The SEC
asks every script to identify itself: set SEC_USER_AGENT="Your Name you@example.com".
Standard library only.
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
TIMEOUT = 180

DEFAULTS = {
    "tickers": ["AAPL", "AXP", "GOOGL", "KO", "BAC"],
    "filer_cik": "1067983",
    "filer_label": "Berkshire Hathaway",
    "members": ["pelosi"],
    "news_hours": 36,
    "top_positions": 10,
    "top_changes": 10,
    "max_headlines": 15,
}

DISCLAIMER = (
    "> هذا التقرير للمعلومات فقط وليس نصيحة مالية. البيانات من مصادر عامة مجانية وقد تتأخر أو تحتوي أخطاء، "
    "وبيانات 13F قديمة (تُنشر بعد 45 يوماً من نهاية الربع). لا يُنفَّذ أي أمر شراء أو بيع من هذا المشروع."
)

STATUS_AR = {
    "NEW": "جديد",
    "ADDED": "زيادة",
    "TRIMMED": "تخفيض",
    "SOLD OUT": "بيع كامل",
    "UNCHANGED": "بدون تغيير",
}
TYPE_AR = {"purchase": "شراء", "sale": "بيع", "sale (partial)": "بيع جزئي", "sale (full)": "بيع كامل",
           "exchange": "تبادل"}


def load_config(path):
    cfg = dict(DEFAULTS)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        cfg["_config_error"] = "تعذّرت قراءة %s: %s" % (path, exc)
    return cfg


def run_json(args):
    """Run one tool and return (data, error). Never raises."""
    cmd = [PY] + args
    try:
        proc = subprocess.run(cmd, cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, "انتهت المهلة (%d ثانية)" % TIMEOUT
    except OSError as exc:
        return None, "تعذّر التشغيل: %s" % exc
    # Some tools exit 1 when a quote failed or a rule rang but still print valid JSON.
    try:
        return json.loads(proc.stdout), None
    except ValueError:
        pass
    text = (proc.stderr or proc.stdout or "").strip().splitlines()
    first = text[0].strip() if text else "خرج برمز %d بلا مخرجات" % proc.returncode
    return None, first[:300]


def money(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "؟"
    for unit, size in (("مليار", 1e9), ("مليون", 1e6)):
        if abs(value) >= size:
            return "%.2f %s$" % (value / size, unit)
    return "{:,.0f}$".format(value)


def cell(text):
    return str(text if text is not None else "").replace("|", "/").replace("\n", " ").strip()


def table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(cell(c) for c in row) + " |" for row in rows]
    return out


def section_alerts(cfg, fx, errors, today):
    lines = ["## تنبيهات الأسعار وحالة السوق", ""]
    args = ["trader-alerts/alerts.py", "check", "--json", "--rules", "trader/alerts.json",
            "--state", "trader/state/alerts.json"]
    if fx:
        args += ["--offline", os.path.join(fx, "trader-alerts"), "--now", today + "T10:00", "--no-write"]
    data, err = run_json(args)
    if err:
        errors.append("التنبيهات: " + err)
        return lines + ["تعذّر تحميل الأسعار: " + err, ""]
    market = data.get("market") or {}
    state = "مفتوح" if market.get("open") else "مغلق"
    lines.append("بورصة نيويورك الآن: **%s** (%s)." % (state, cell(market.get("reason"))))
    if not market.get("open") and market.get("next_open"):
        lines.append("الافتتاح القادم: %s (بتوقيت نيويورك)." % market["next_open"])
    lines.append("")
    rings = data.get("rings") or []
    if rings:
        lines.append("**تنبيهات فعّالة:**")
        lines += ["- %s" % cell(r.get("message")) for r in rings]
        lines.append("")
    quotes = data.get("quotes") or {}
    if quotes:
        lines += table(["السهم", "آخر سعر (متأخر)"], [(s, "%.2f$" % p) for s, p in sorted(quotes.items())])
        lines.append("")
    seen = set()
    for e in data.get("errors") or []:
        if e not in seen:
            seen.add(e)
            errors.append("أسعار: " + cell(e))
    return lines


def section_news(cfg, fx, errors, today):
    lines = ["## أخبار أسهمك", ""]
    args = ["trader-news/news.py", "--json", "--tickers"] + list(cfg["tickers"]) + [
        "--hours", str(cfg["news_hours"]), "--state", "trader/state/news.json"]
    if fx:
        args += ["--offline", os.path.join(fx, "trader-news"), "--now", today + "T23:59:00+00:00",
                 "--hours", "2400", "--dry-run"]
    data, err = run_json(args)
    if err:
        errors.append("الأخبار: " + err)
        return lines + ["تعذّر تحميل الأخبار: " + err, ""]
    items = [i for i in (data.get("items") or []) if i.get("new", True)]
    for note in data.get("notes") or []:
        errors.append("أخبار: " + cell(note))
    if not items:
        return lines + ["لا أخبار جديدة منذ آخر تقرير.", ""]
    items.sort(key=lambda i: i.get("time") or "", reverse=True)
    shown = items[: int(cfg["max_headlines"])]
    for it in shown:
        tags = " ".join("`%s`" % t for t in (it.get("tags") or []))
        kind = "ملف SEC" if it.get("kind") != "headline" else "خبر"
        title = cell(it.get("title"))
        link = it.get("link")
        head = "[%s](%s)" % (title, link) if link else title
        lines.append("- **%s** · %s · %s %s" % (", ".join(it.get("touches") or [it.get("ticker", "")]), kind, head, tags))
    if len(items) > len(shown):
        lines.append("- … و %d عناوين أخرى." % (len(items) - len(shown)))
    lines.append("")
    return lines


def section_congress(cfg, fx, errors, today):
    lines = ["## صفقات الكونغرس (إفصاحات علنية)", ""]
    args = ["trader-pelosi/ptr.py", "new", "--json", "--read", "--state", "trader/state/pelosi.json",
            "--tickers"] + list(cfg["tickers"])
    for member in cfg["members"]:
        args += ["--member", member]
    if fx:
        args = ["trader-pelosi/ptr.py", "new", "--json", "--dry-run", "--year", today[:4],
                "--offline", os.path.join(fx, "trader-pelosi", "2026FD.zip"), "--today", today]
        for member in cfg["members"]:
            args += ["--member", member]
    data, err = run_json(args)
    if err:
        errors.append("الكونغرس: " + err)
        return lines + ["تعذّر تحميل الإفصاحات: " + err, ""]
    fresh = data.get("new") or []
    if not fresh:
        return lines + ["لا إفصاحات جديدة.", ""]
    reports = {(r.get("filing") or {}).get("doc_id"): r.get("report") for r in data.get("reports") or []}
    for f in fresh:
        lines.append("### %s - قُدِّم في %s" % (cell(f.get("name")), cell(f.get("filed"))))
        lines.append("[ملف PDF](%s)" % f.get("url"))
        rep = reports.get(f.get("doc_id"))
        rows = (rep or {}).get("rows") or []
        if rows:
            lines.append("")
            lines += table(["التاريخ", "النوع", "السهم", "الأصل", "المبلغ", "التبليغ بعد"],
                           [(r.get("date"), TYPE_AR.get(r.get("type_name"), r.get("type_name")),
                             r.get("ticker") or "-", r.get("asset"), r.get("amount"),
                             "%s يوم%s" % (r.get("days_to_disclose"), " (متأخر)" if r.get("late") else ""))
                            for r in rows[:25]])
        else:
            lines.append("")
            lines.append("لم تُقرأ تفاصيل الصفقات (يلزم `pdftotext`)؛ افتح الملف أعلاه.")
        lines.append("")
    return lines


def section_13f(cfg, fx, errors, today):
    label = cfg["filer_label"]
    lines = ["## محفظة %s (13F)" % label, ""]
    base = ["trader-copycat/edgar13f.py"]
    tail = ["--json"]
    if fx:
        tail += ["--offline", os.path.join(fx, "trader-copycat"), "--map", "/nonexistent.json"]
    cik = str(cfg["filer_cik"])
    hold, err = run_json(base + ["holdings", cik, "--combine", "--top", str(cfg["top_positions"])] + tail)
    if err:
        errors.append("13F: " + err)
        return lines + ["تعذّر تحميل المحفظة: " + err, ""]
    meta = hold.get("meta") or {}
    lines.append("آخر تقرير: ربع **%s** (قُدِّم %s). هذه مراكز قديمة وليست ما يملكه الآن." % (
        cell(meta.get("period")), cell(meta.get("filed"))))
    lines.append("")
    total = hold.get("total") or 0
    lines += table(["السهم", "الشركة", "القيمة", "% من المحفظة"],
                   [(p.get("ticker") or "-", p.get("name") or p.get("issuer"), money(p.get("value")),
                     "%.2f%%" % (p.get("pct_of_13f") or 0)) for p in (hold.get("positions") or [])])
    lines += ["", "إجمالي المراكز: %s" % money(total), ""]
    diff, derr = run_json(base + ["diff", cik, "--combine"] + tail)
    if derr:
        errors.append("13F (التغيرات): " + derr)
        return lines
    changes = [c for c in (diff.get("changes") or []) if c.get("status") != "UNCHANGED"]
    changes.sort(key=lambda c: abs((c.get("value_after") or 0) - (c.get("value_before") or 0)), reverse=True)
    if changes:
        lines.append("**التغيرات عن الربع السابق (%s):**" % cell((diff.get("meta") or {}).get("prior_period")))
        lines.append("")
        lines += table(["الحالة", "السهم", "الشركة", "التغير في عدد الأسهم", "القيمة الحالية"],
                       [(STATUS_AR.get(c["status"], c["status"]), c.get("ticker") or "-", c.get("issuer"),
                         ("%+.1f%%" % c["change_pct"]) if c.get("change_pct") is not None else "-",
                         money(c.get("value_after")))
                        for c in changes[: int(cfg["top_changes"])]])
        lines.append("")
    return lines


def build(cfg, fx, today):
    errors = []
    body = []
    for section in (section_alerts, section_news, section_congress, section_13f):
        try:
            body += section(cfg, fx, errors, today)
        except Exception as exc:  # a broken section must not stop the report
            errors.append("%s: خطأ غير متوقع: %s" % (section.__name__, exc))
    head = ["# التقرير اليومي - %s" % today, "",
            "أسهم المتابعة: " + "، ".join(cfg["tickers"]), ""]
    if cfg.get("_config_error"):
        errors.insert(0, cfg["_config_error"])
    tail = []
    if errors:
        tail += ["## ملاحظات وأخطاء", ""] + ["- " + cell(e) for e in errors] + [""]
    tail += ["---", DISCLAIMER, ""]
    return "\n".join(head + body + tail), errors


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", default=os.path.join(ROOT, "trader", "report.json"))
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "reports"))
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default: today, UTC)")
    ap.add_argument("--fixtures", default=None, help="read saved test data instead of the network")
    ap.add_argument("--stdout", action="store_true", help="print the report instead of writing a file")
    args = ap.parse_args(argv)

    today = args.date or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    cfg = load_config(args.config)
    fx = os.path.abspath(args.fixtures) if args.fixtures else None
    text, errors = build(cfg, fx, today)
    if args.stdout:
        sys.stdout.write(text)
        return 0
    os.makedirs(args.out_dir, exist_ok=True)
    path = os.path.join(args.out_dir, today + ".md")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)
    print("wrote %s (%d problems)" % (os.path.relpath(path, ROOT), len(errors)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
