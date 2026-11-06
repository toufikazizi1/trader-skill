"""Offline tests for scripts/gold_report.py. The candles and headlines in
tests/fixtures/trader-gold are synthetic: no network, no real prices."""
import contextlib
import importlib.util
import io
import os
import tempfile
import unittest
import datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "trader-gold")

spec = importlib.util.spec_from_file_location("gold_report", os.path.join(ROOT, "scripts", "gold_report.py"))
gold = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gold)

NOW = dt.datetime(2026, 11, 13, 23, tzinfo=dt.timezone.utc)


def candles(closes, spread=5.0):
    return [{"t": i, "o": c, "h": c + spread, "l": c - spread, "c": c, "v": 100.0} for i, c in enumerate(closes)]


def run(args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = gold.main(args)
    return code, out.getvalue()


class GoldReportTests(unittest.TestCase):
    def test_report_has_every_section_and_only_gold(self):
        code, text = run(["--fixtures", FIX, "--date", "2026-11-13", "--stdout"])
        self.assertEqual(code, 0)
        for heading in ("## الملخص", "## التحليل", "## سيناريوهات للدراسة", "## أخبار الذهب بالعربية",
                        "## أخبار الذهب بالإنجليزية", "نصيحة مالية"):
            self.assertIn(heading, text)
        for stock in ("AAPL", "Berkshire", "Pelosi", "GOOGL"):
            self.assertNotIn(stock, text)

    def test_old_headlines_are_dropped(self):
        with open(os.path.join(FIX, "news-en.xml"), encoding="utf-8") as fh:
            items = gold.parse_news(fh.read(), 48, NOW)
        titles = [i["title"] for i in items]
        self.assertEqual(len(items), 2)
        self.assertNotIn("Old synthetic headline", titles)
        self.assertEqual(titles[0], "Synthetic test headline one about gold")
        self.assertEqual(items[0]["source"], "Example Wire")

    def test_writes_dated_and_latest_files_without_touching_state_in_fixture_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "state.json")
            code, _ = run(["--fixtures", FIX, "--date", "2026-11-13", "--out-dir", tmp, "--state", state])
            self.assertEqual(code, 0)
            for name in ("2026-11-13.md", "latest.md"):
                with open(os.path.join(tmp, name), encoding="utf-8") as fh:
                    self.assertTrue(fh.read().startswith("# تقرير الذهب - 2026-11-13"))
            self.assertFalse(os.path.exists(state))

    def test_round_level_crossings(self):
        self.assertEqual(gold.crossed(4290, 4410, 100), [4300, 4400])
        self.assertEqual(gold.crossed(4410, 4290, 100), [4400, 4300])
        self.assertEqual(gold.crossed(4310, 4390, 100), [])
        self.assertEqual(gold.crossed(None, 4300, 100), [])
        self.assertEqual(gold.crossed(4300, 4300, 100), [])

    def test_trend_structure_and_verdict_on_a_clean_uptrend(self):
        zig = []
        price = 3000.0
        for i in range(120):
            price += 12 if i % 10 < 7 else -6
            zig.append(price)
        cs = candles(zig, 4.0)
        an = gold.analyse(cs, zig[-1], dict(gold.DEFAULTS))
        self.assertEqual(an["trend"], "صاعد")
        self.assertIn("صاعد", an["structure"])
        self.assertEqual(an["verdict"], "مائل للصعود")

    def test_downtrend_is_read_as_down(self):
        zig = []
        price = 5000.0
        for i in range(120):
            price -= 12 if i % 10 < 7 else -6
            zig.append(price)
        an = gold.analyse(candles(zig, 4.0), zig[-1], dict(gold.DEFAULTS))
        self.assertEqual(an["trend"], "هابط")
        self.assertEqual(an["verdict"], "مائل للهبوط")

    def test_flat_market_waits(self):
        flat = [4000 + (5 if i % 2 else -5) for i in range(120)]
        an = gold.analyse(candles(flat), flat[-1], dict(gold.DEFAULTS))
        self.assertEqual(an["verdict"], "حياد / انتظار")

    def test_scenarios_put_stops_on_the_right_side(self):
        zig = [4000 + (20 if i % 6 < 3 else -20) for i in range(120)]
        an = gold.analyse(candles(zig, 6.0), 4000.0, dict(gold.DEFAULTS))
        (_, _, long_stop, long_target), (_, _, short_stop, short_target) = gold.scenarios(4000.0, an, gold.DEFAULTS)
        self.assertLess(long_stop, an["resist"])
        self.assertGreater(long_target, an["resist"])
        self.assertGreater(short_stop, an["support"])
        self.assertLess(short_target, an["support"])

    def test_value_area_needs_volume(self):
        cs = candles([4000 + i for i in range(80)])
        for c in cs:
            c["v"] = 0.0
        self.assertIsNone(gold.value_area(cs, 70))
        cs2 = candles([4000 + i for i in range(80)])
        poc, vah, val = gold.value_area(cs2, 70)
        self.assertLessEqual(val, poc)
        self.assertLessEqual(poc, vah)

    def test_a_dead_source_is_reported_not_raised(self):
        real = gold.read_source

        def boom(*a, **k):
            raise RuntimeError("boom")
        gold.read_source = boom
        try:
            text, errors = gold.build(dict(gold.DEFAULTS), None, "2026-11-13", {})
        finally:
            gold.read_source = real
        self.assertIn("ملاحظات وأخطاء", text)
        self.assertEqual(len(errors), 3)

    def test_bad_chart_shapes_are_refused(self):
        for bad in ("not json", '{"chart": {"result": null}}', '{"chart": {"result": []}}'):
            with self.assertRaises(RuntimeError):
                gold.parse_chart(bad)

    def test_pipes_and_newlines_cannot_break_a_table(self):
        self.assertEqual(gold.cell("a|b\nc"), "a/b c")


if __name__ == "__main__":
    unittest.main()
