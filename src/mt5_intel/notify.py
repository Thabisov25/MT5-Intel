"""Notifications: Windows toast + optional email. Deduped via Store.should_notify.

Senders catch every exception: a broken notification channel must never crash a watch cycle.
Sensitive values (email password) stay in .env and are never logged.
"""

from __future__ import annotations

import os
import smtplib
import subprocess
from email.mime.text import MIMEText

from .store import Store


def _toast_windows(title: str, message: str) -> None:
    script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml('<toast><visual><binding template="ToastText02"><text id="1">{title}</text><text id="2">{message}</text></binding></visual></toast>')
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("MT5 Intel").Show($toast)
"""
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        timeout=15,
        check=False,  # a failed toast is a dropped alert, not a crash
    )


def send_toast(title: str, message: str) -> bool:
    try:
        if os.name != "nt":
            return False
        _toast_windows(_xml_escape(title), _xml_escape(message))
        return True
    except Exception:  # noqa: BLE001 — blueprint: a failed alert must never crash the watch cycle
        return False


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_email(subject: str, body: str) -> bool:
    host = os.environ.get("SMTP_HOST", "").strip()
    if not host:
        return False
    try:
        port = int(os.environ.get("SMTP_PORT", "587"))
        user = os.environ.get("SMTP_USER", "").strip()
        password = os.environ.get("SMTP_PASSWORD", "").strip()
        sender = os.environ.get("SMTP_FROM", user).strip()
        recipients = [r.strip() for r in os.environ.get("SMTP_TO", "").split(",") if r.strip()]
        if not (user and password and recipients):
            return False
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = ", ".join(recipients)
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(sender, recipients, msg.as_string())
        return True
    except Exception:  # noqa: BLE001 — blueprint: email failures must not crash market monitoring
        return False


def alert(
    store: Store,
    kind: str,
    title: str,
    message: str,
    *,
    toast: bool = True,
    email: bool = True,
    cooldown_minutes: int = 30,
) -> bool:
    """Send an alert once per cooldown window for an identical signature."""
    signature = f"{kind}:{message[:80]}"
    if not store.should_notify(signature):
        return False
    results = {}
    if toast:
        results["toast"] = send_toast(title, message)
    if email:
        results["email"] = send_email(title, message)
    store.append_event("notify", {"kind": kind, "title": title, "channels": results, "message": message})
    return True
