"""
notify.py — RateGuard 多渠道告警通知

支持：飞书机器人（diagram）、钉钉、企业微信、邮件
"""
from __future__ import annotations

import logging
from typing import Any

import requests

from src.config import config

logger = logging.getLogger("rateguard")


# ══════════════════════════════════════════════════════════════════════════
# 统一告警入口
# ══════════════════════════════════════════════════════════════════════════

def send_alert(title: str, body: str, level: str = "warning") -> None:
    """按平台分发告警：飞书 → 企业微信 → 钉钉 → 邮件"""
    # 1. 飞书机器人
    lark = config.get("notifications.lark") or {}
    if _enabled(lark) and lark.get("webhook"):
        try:
            _send_lark(lark["webhook"], title, body)
        except Exception as exc:
            logger.warning(f"[lark] 通知异常: {exc}")

    # 2. 企业微信群机器人（同格式 webhook，端点不同）
    wecom = config.get("notifications.wecom_group") or {}
    if _enabled(wecom) and wecom.get("webhook"):
        try:
            _send_wecom_group(wecom["webhook"], title, body)
        except Exception as exc:
            logger.warning(f"[wecom] 通知异常: {exc}")

    # 3. 钉钉机器人
    ding = config.get("notifications.dingtalk") or {}
    if _enabled(ding) and ding.get("webhook"):
        try:
            _send_dingtalk(ding["webhook"], title, body)
        except Exception as exc:
            logger.warning(f"[dingtalk] 通知异常: {exc}")

    # 4. 邮件
    mail = config.get("notifications.email") or {}
    if _enabled(mail):
        try:
            _send_email(title, body, mail)
        except Exception as exc:
            logger.warning(f"[email] 通知异常: {exc}")

    if not _is_any_enabled(lark, wecom, ding, mail):
        logger.info(f"[notify] {title}: {body[:120]}")  # noqa: G004


def _enabled(cfg: dict) -> bool:
    return bool(cfg and cfg.get("enabled"))


def _is_any_enabled(*cfgs) -> bool:
    return any(_enabled(c) for c in cfgs)


# ══════════════════════════════════════════════════════════════════════════
# 具体通知层
# ══════════════════════════════════════════════════════════════════════════

def _post_webhook(url: str, payload: dict) -> dict:
    r = requests.post(url, json=payload, timeout=10)
    r.raise_for_status()
    return r.json()


def _send_lark(webhook: str, title: str, body: str) -> dict:
    """飞书自定义机器人 webhook（富文本卡片）"""
    return _post_webhook(
        webhook,
        {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": "orange",
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {"tag": "lark_md", "content": body[:3000]},
                    }
                ],
            },
        },
    )


def _send_wecom_group(webhook: str, title: str, body: str) -> dict:
    """企业微信群 Markdown 机器人"""
    return _post_webhook(
        webhook,
        {
            "msgtype": "markdown",
            "markdown": {"content": f"**{title}**\n{body[:3000]}"},
        },
    )


def _send_dingtalk(webhook: str, title: str, body: str) -> dict:
    """钉钉自定义机器人（markdown）"""
    return _post_webhook(
        webhook,
        {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": f"**{title}**\n{body[:3000]}"},
        },
    )


def _send_email(subject: str, body: str, cfg: dict) -> None:
    import smtplib, ssl
    from email.message import EmailMessage

    host = cfg["smtp_host"]
    port = int(cfg.get("smtp_port", 587))
    user = str(cfg.get("smtp_user", ""))
    pw = str(cfg.get("smtp_pass", ""))
    frm = cfg.get("from_addr", "") or user
    to = cfg["to_addr"]

    msg = EmailMessage()
    msg["Subject"] = f"[RateGuard] {subject}"
    msg["From"] = frm
    msg["To"] = to
    msg.set_content(body)

    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=15) as smtp:
        if port == 587:
            smtp.starttls(context=ctx)
        smtp.login(user, pw)
        smtp.send_message(msg)
