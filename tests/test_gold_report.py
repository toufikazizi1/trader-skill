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

    def test_local_jewellery_headlines_are_excluded(self):
        xml = (
            '<rss><channel>'
            '<item><title>سعر الذهب عيار 21 اليوم - مصدر</title><link>https://example.com/1</link>'
            '<pubDate>Fri, 13 Nov 2026 20:00:00 GMT</pubDate><source>مصدر</source></item>'
            '<item><title>الذهب يتجاوز 4100 دولار للأوقية - مصدر</title><link>https://example.com/2</link>'
            '<pubDate>Fri, 13 Nov 2026 20:00:00 GMT</pubDate><source>مصدر</source></item>'
            '</channel></rss>')
        items = gold.parse_news(xml, 48, NOW, gold.DEFAULTS["news_exclude"])
        self.assertEqual([i["title"] for i in items], ["الذهب يتجاوز 4100 دولار للأوقية"])
        self.assertEqual(len(gold.parse_news(xml, 48, NOW)), 2)

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

    def test_summary_carries_entry_scenarios_with_stop_and_target(self):
        _, text = run(["--fixtures", FIX, "--date", "2026-11-13", "--stdout"])
        summary = text.split("## التحليل")[0]
        self.assertIn("سيناريو صعودي", summary)
        self.assertIn("سيناريو هبوطي", summary)
        self.assertIn("وقف الخسارة", summary)
        self.assertIn("هدف 1:2", summary)

    def test_merge_news_drops_repeats_and_keeps_newest_first(self):
        old = dt.datetime(2026, 11, 13, 1, tzinfo=dt.timezone.utc)
        new = dt.datetime(2026, 11, 13, 9, tzinfo=dt.timezone.utc)
        a = [{"title": "Gold  up", "link": "https://x/1", "source": "", "time": old}]
        b = [{"title": "gold up", "link": "https://x/2", "source": "", "time": old},
             {"title": "Other", "link": "https://x/1", "source": "", "time": old},
             {"title": "Fresh", "link": "https://x/3", "source": "", "time": new}]
        merged = gold.merge_news([a, b])
        self.assertEqual([i["title"] for i in merged], ["Fresh", "Gold  up"])

    def test_alerts_are_recorded_for_the_notifier(self):
        cfg = dict(gold.DEFAULTS)
        state = {"last_price": 4000.0, "last_verdict": "صعودي"}
        gold.build(cfg, FIX, "2026-11-13", state)
        self.assertIn("level", state["notify"])
        self.assertIn("verdict", state["notify"])
        quiet = {}
        gold.build(cfg, FIX, "2026-11-13", quiet)
        self.assertNotIn("verdict", quiet["notify"])

    def test_price_and_levels_come_from_spot_not_the_future(self):
        _, text = run(["--fixtures", FIX, "--date", "2026-11-13", "--stdout"])
        self.assertIn("4,289.6$", text)  # spot fixture is 15 dollars under the future's 4,304.6
        self.assertNotIn("4,304.6$", text)
        self.assertIn("الذهب الفوري XAUUSD", text)

    def test_value_area_still_works_from_the_futures_volume(self):
        _, text = run(["--fixtures", FIX, "--date", "2026-11-13", "--stdout"])
        self.assertNotIn("غير متاح", text)

    def test_falls_back_to_the_future_and_says_so(self):
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            for name in os.listdir(FIX):
                if "XAUUSD" not in name and not name.startswith("spot-"):
                    shutil.copy(os.path.join(FIX, name), tmp)
            _, text = run(["--fixtures", tmp, "--date", "2026-11-13", "--stdout"])
        self.assertIn("4,304.6$", text)
        self.assertIn("السعر الفوري غير متاح", text)

    def test_price_offset_moves_price_and_levels_together(self):
        errors = []
        price, prev, candles, vol, source = gold.market(FIX, dict(gold.DEFAULTS, price_offset=-10.0), errors)
        base = gold.market(FIX, dict(gold.DEFAULTS), [])
        self.assertAlmostEqual(price, base[0] - 10.0)
        self.assertAlmostEqual(candles[-1]["h"], base[2][-1]["h"] - 10.0)
        self.assertAlmostEqual(vol[-1]["l"], base[3][-1]["l"] - 10.0)
        self.assertIn("-10.0", source)

    def test_both_sources_dead_is_an_error_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                gold.market(tmp, dict(gold.DEFAULTS), [])

    def _copy_fixtures_without(self, tmp, *drop):
        import shutil
        for name in os.listdir(FIX):
            if not any(d in name for d in drop):
                shutil.copy(os.path.join(FIX, name), tmp)

    def test_stooq_candles_work_when_yahoo_spot_is_gone(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._copy_fixtures_without(tmp, "XAUUSD=X")
            _, text = run(["--fixtures", tmp, "--date", "2026-11-13", "--stdout"])
        self.assertIn("4,289.6$", text)
        self.assertIn("الذهب الفوري XAUUSD", text)
        self.assertNotIn("## ملاحظات وأخطاء", text)

    def test_spot_price_alone_moves_the_futures_levels_onto_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._copy_fixtures_without(tmp, "XAUUSD=X", "stooq")
            _, text = run(["--fixtures", tmp, "--date", "2026-11-13", "--stdout"])
        self.assertIn("4,289.6$", text)
        self.assertIn("بعد إزاحتها -15.0$", text)
        self.assertIn("شموع الذهب الفوري غير متاحة", text)
        self.assertNotIn("4,304.6$", text)

    def test_second_price_source_is_used_when_the_first_is_dead(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._copy_fixtures_without(tmp, "XAUUSD=X", "goldapi", "stooq-daily")
            price, _, _, _, source = gold.market(tmp, dict(gold.DEFAULTS), [])
        self.assertAlmostEqual(price, 4289.6)

    def test_source_parsers_refuse_junk(self):
        for fn, bad in ((gold.parse_goldapi, "{}"), (gold.parse_goldapi, '{"price": 0}'),
                        (gold.parse_stooq_quote, "Symbol,Date,Close\nXAUUSD,N/D,N/D"),
                        (gold.parse_stooq_quote, "<html>blocked</html>"),
                        (gold.parse_stooq_daily, "Date,Open,High,Low,Close\n2026-01-01,1,2,0.5,1.5")):
            with self.assertRaises(RuntimeError, msg=bad):
                fn(bad)


if __name__ == "__main__":
    unittest.main()
