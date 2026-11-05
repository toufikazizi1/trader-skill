"""Offline tests for scripts/daily_report.py (saved fixtures, no network)."""
import contextlib
import importlib.util
import io
import os
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures")

spec = importlib.util.spec_from_file_location("daily_report", os.path.join(ROOT, "scripts", "daily_report.py"))
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def run(args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = report.main(args)
    return code, out.getvalue()


class DailyReportTests(unittest.TestCase):
    def test_report_from_fixtures_has_every_section(self):
        code, text = run(["--fixtures", FIX, "--date", "2026-10-13", "--stdout"])
        self.assertEqual(code, 0)
        for heading in ("## تنبيهات الأسعار", "## أخبار أسهمك", "## صفقات الكونغرس", "## محفظة", "نصيحة مالية"):
            self.assertIn(heading, text)
        self.assertIn("AAPL", text)
        self.assertIn("22.04%", text)

    def test_writes_dated_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = run(["--fixtures", FIX, "--date", "2026-10-13", "--out-dir", tmp])
            self.assertEqual(code, 0)
            path = os.path.join(tmp, "2026-10-13.md")
            self.assertTrue(os.path.exists(path))
            with open(path, encoding="utf-8") as fh:
                self.assertTrue(fh.read().startswith("# التقرير اليومي - 2026-10-13"))

    def test_a_failing_tool_is_reported_not_raised(self):
        real = report.run_json
        report.run_json = lambda args: (None, "boom")
        try:
            text, errors = report.build(dict(report.DEFAULTS), None, "2026-10-13")
        finally:
            report.run_json = real
        self.assertIn("ملاحظات وأخطاء", text)
        self.assertTrue(any("boom" in e for e in errors))

    def test_config_overrides_and_bad_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = os.path.join(tmp, "g.json")
            with open(good, "w", encoding="utf-8") as fh:
                fh.write('{"tickers": ["KO"], "unknown": 1}')
            cfg = report.load_config(good)
            self.assertEqual(cfg["tickers"], ["KO"])
            self.assertNotIn("unknown", cfg)
            bad = os.path.join(tmp, "b.json")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            self.assertIn("_config_error", report.load_config(bad))
            self.assertEqual(report.load_config(os.path.join(tmp, "missing.json"))["tickers"],
                             report.DEFAULTS["tickers"])

    def test_money_and_cells(self):
        self.assertEqual(report.money(65950296923.0), "65.95 مليار$")
        self.assertEqual(report.money(None), "؟")
        self.assertEqual(report.cell("a|b\nc"), "a/b c")


if __name__ == "__main__":
    unittest.main()
