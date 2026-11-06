#!/usr/bin/env python3
"""Send the summary of reports/latest.md to Telegram.

    python3 scripts/telegram_send.py [reports/latest.md] [--only-alerts]

With --only-alerts it sends only when the last run raised an alert (a round level was crossed, the
price is near support or resistance, or the verdict changed), read from trader/state/gold.json.

Needs two environment variables (GitHub Actions secrets in the workflow):
TELEGRAM_BOT_TOKEN from @BotFather and TELEGRAM_CHAT_ID. Without them it says so and
exits 0, so the workflow still works before Telegram is set up. The token is never
printed. Standard library only.
"""
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIMIT = 4000  # Telegram allows 4096 characters per message


def summary_lines(text):
    """The non-empty lines under '## الملخص', up to the next heading."""
    lines = text.splitlines()
    try:
        start = lines.index("## الملخص") + 1
    except ValueError:
        return []
    out = []
    for line in lines[start:]:
        if line.startswith("#"):
            break
        if line.strip():
            out.append(line.strip())
    return out


def to_html(line):
    """One markdown line -> Telegram HTML (bold, links, bullets). Escapes everything else."""
    s = html.escape(line, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
               lambda m: '<a href="%s">%s</a>' % (m.group(2).replace('"', "%22"), m.group(1)), s)
    return "• " + s[2:] if s.startswith("- ") else s


def build_message(text, repo=None):
    title = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")), "تقرير الذهب")
    parts = ["<b>%s</b>" % html.escape(title, quote=False), ""]
    parts += [to_html(l) for l in summary_lines(text)]
    if repo:
        parts += ["", '<a href="https://github.com/%s/blob/main/reports/latest.md">التقرير الكامل</a>' % repo]
    msg = "\n".join(parts)
    if len(msg) > LIMIT:
        msg = msg[:LIMIT].rsplit("\n", 1)[0]
    return msg


def post_json(url, payload, timeout=20):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read(100_000).decode("utf-8", "replace"))


def send(token, chat_id, message, post=None):
    """Send one message. Raises RuntimeError without ever including the token."""
    post = post or post_json
    url = "https://api.telegram.org/bot%s/sendMessage" % token
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        reply = post(url, payload)
    except urllib.error.HTTPError as exc:
        raise RuntimeError("Telegram أجاب HTTP %d (تأكد من التوكن ومن رقم المحادثة، وأنك أرسلت رسالة للبوت أولاً)"
                           % exc.code)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise RuntimeError("تعذّر الاتصال بـ Telegram: %s" % type(exc).__name__)
    if not isinstance(reply, dict) or not reply.get("ok"):
        raise RuntimeError("Telegram رفض الرسالة: %s" % str((reply or {}).get("description", "؟"))[:200])


def has_alert(state_path):
    try:
        with open(state_path, encoding="utf-8") as fh:
            notify = json.load(fh).get("notify")
    except (OSError, ValueError, AttributeError):
        return False
    return bool(notify)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    only_alerts = "--only-alerts" in argv
    argv = [a for a in argv if a != "--only-alerts"]
    path = argv[0] if argv else os.path.join(ROOT, "reports", "latest.md")
    state_path = os.path.join(ROOT, "trader", "state", "gold.json")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        print("Telegram غير مفعّل: أضف TELEGRAM_BOT_TOKEN و TELEGRAM_CHAT_ID في Secrets.")
        return 0
    if only_alerts and not has_alert(state_path):
        print("لا تنبيه جديد، لم يُرسل شيء.")
        return 0
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        print("تعذّرت قراءة %s" % path, file=sys.stderr)
        return 1
    if not summary_lines(text):
        print("لا ملخص في التقرير، لم يُرسل شيء.", file=sys.stderr)
        return 1
    try:
        send(token, chat, build_message(text, os.environ.get("GITHUB_REPOSITORY")))
    except RuntimeError as exc:
        print(str(exc).replace(token, "***"), file=sys.stderr)
        return 1
    print("أُرسل الملخص إلى Telegram.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
