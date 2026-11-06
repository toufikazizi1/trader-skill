"""Offline tests for scripts/telegram_send.py: nothing here touches the network."""
import contextlib
import importlib.util
import io
import os
import tempfile
import unittest
import urllib.error
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("telegram_send", os.path.join(ROOT, "scripts", "telegram_send.py"))
tg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tg)

REPORT = """# تقرير الذهب - 2026-11-13

## الملخص

- **السعر:** 4,053.5$ (+1.57%)
- **الحكم الحالي:** حياد / انتظار. a < b & c
- **أحدث خبر:** [عنوان "مهم"](https://example.com/a?x=1&y=2)

## التحليل

| المؤشر | القيمة |
"""


class TelegramTests(unittest.TestCase):
    def test_summary_stops_at_the_next_heading(self):
        lines = tg.summary_lines(REPORT)
        self.assertEqual(len(lines), 3)
        self.assertNotIn("المؤشر", " ".join(lines))
        self.assertEqual(tg.summary_lines("# no summary here"), [])

    def test_html_is_escaped_and_markdown_converted(self):
        msg = tg.build_message(REPORT, "me/repo")
        self.assertIn("<b>تقرير الذهب - 2026-11-13</b>", msg)
        self.assertIn("• <b>السعر:</b> 4,053.5$", msg)
        self.assertIn("a &lt; b &amp; c", msg)
        self.assertIn('<a href="https://example.com/a?x=1&amp;y=2">عنوان "مهم"</a>', msg)
        self.assertIn("https://github.com/me/repo/blob/main/reports/latest.md", msg)
        self.assertNotIn("##", msg)

    def test_message_is_cut_below_telegram_limit(self):
        big = "# t\n\n## الملخص\n\n" + "\n".join("- line %d %s" % (i, "x" * 100) for i in range(200))
        self.assertLessEqual(len(tg.build_message(big)), tg.LIMIT)

    def test_send_posts_the_expected_payload(self):
        seen = {}

        def fake(url, payload):
            seen.update(url=url, payload=payload)
            return {"ok": True}
        tg.send("123:abc", "42", "hello", post=fake)
        self.assertTrue(seen["url"].endswith("/bot123:abc/sendMessage"))
        self.assertEqual(seen["payload"]["chat_id"], "42")
        self.assertEqual(seen["payload"]["parse_mode"], "HTML")

    def test_errors_never_contain_the_token(self):
        def http_error(url, payload):
            raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)

        def refused(url, payload):
            return {"ok": False, "description": "chat not found"}
        for fake in (http_error, refused):
            with self.assertRaises(RuntimeError) as ctx:
                tg.send("SECRET-TOKEN", "42", "x", post=fake)
            self.assertNotIn("SECRET-TOKEN", str(ctx.exception))

    def test_main_skips_without_secrets_and_never_raises(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}):
            with contextlib.redirect_stdout(out):
                self.assertEqual(tg.main(["/nonexistent.md"]), 0)
        self.assertIn("غير مفعّل", out.getvalue())

    def test_main_sends_and_hides_the_token_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "latest.md")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(REPORT)
            env = {"TELEGRAM_BOT_TOKEN": "SECRET-TOKEN", "TELEGRAM_CHAT_ID": "42"}
            with mock.patch.dict(os.environ, env):
                with mock.patch.object(tg, "post_json", return_value={"ok": True}):
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(tg.main([path]), 0)

                def boom(url, payload):
                    raise urllib.error.URLError("failed for " + url)
                err = io.StringIO()
                with mock.patch.object(tg, "post_json", boom), contextlib.redirect_stderr(err):
                    self.assertEqual(tg.main([path]), 1)
                self.assertNotIn("SECRET-TOKEN", err.getvalue())


if __name__ == "__main__":
    unittest.main()
