"""Branded HTML email layouts for Chic A Boo."""

from __future__ import annotations


def _shell(*, title: str, preheader: str, body_html: str, site_url: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
</head>
<body style="margin:0;padding:0;background:#f5efeb;font-family:Georgia,'Times New Roman',serif;">
  <span style="display:none;max-height:0;overflow:hidden;opacity:0;">{preheader}</span>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f5efeb;padding:32px 16px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border-radius:16px;overflow:hidden;border:1px solid rgba(193,155,84,0.25);">
          <tr>
            <td style="padding:28px 28px 12px;text-align:center;background:linear-gradient(180deg,#fff8f2,#ffffff);">
              <a href="{site_url}" style="text-decoration:none;color:#c19b54;font-size:28px;font-style:italic;letter-spacing:0.04em;">CHIC A BOO</a>
              <p style="margin:8px 0 0;font-size:12px;letter-spacing:0.18em;text-transform:uppercase;color:#946a2b;font-family:Arial,sans-serif;">Handmade with care</p>
            </td>
          </tr>
          <tr>
            <td style="padding:8px 28px 32px;color:#5c4a3a;font-size:16px;line-height:1.6;font-family:Arial,Helvetica,sans-serif;">
              {body_html}
            </td>
          </tr>
          <tr>
            <td style="padding:18px 28px 28px;border-top:1px solid rgba(193,155,84,0.2);text-align:center;font-family:Arial,sans-serif;font-size:12px;color:#9a8776;">
              <p style="margin:0 0 6px;">With love from the sisters at Chic A Boo</p>
              <p style="margin:0;"><a href="{site_url}" style="color:#c19b54;text-decoration:none;">{site_url.replace("https://","").replace("http://","")}</a></p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def verification_otp_email(*, otp: str, expires_minutes: int, site_url: str) -> tuple[str, str]:
    subject = "Your Chic A Boo verification code"
    body = f"""
      <h1 style="margin:0 0 12px;font-size:22px;color:#5c4a3a;font-family:Georgia,serif;">Welcome — almost there</h1>
      <p style="margin:0 0 16px;">Thanks for joining Chic A Boo. Use this one-time code to verify your email:</p>
      <p style="margin:24px 0;text-align:center;">
        <span style="display:inline-block;padding:14px 28px;border-radius:12px;background:#f5efeb;border:1px solid #e3c6c6;font-size:32px;letter-spacing:10px;font-weight:700;color:#946a2b;font-family:Georgia,serif;">{otp}</span>
      </p>
      <p style="margin:0 0 8px;">This code expires in <strong>{expires_minutes} minutes</strong>.</p>
      <p style="margin:0;color:#9a8776;font-size:14px;">If you didn’t create an account, you can safely ignore this email.</p>
    """
    return subject, _shell(
        title=subject,
        preheader=f"Your verification code is {otp}",
        body_html=body,
        site_url=site_url,
    )


def password_reset_email(*, reset_url: str, expires_minutes: int, site_url: str) -> tuple[str, str]:
    subject = "Reset your Chic A Boo password"
    body = f"""
      <h1 style="margin:0 0 12px;font-size:22px;color:#5c4a3a;font-family:Georgia,serif;">Password reset</h1>
      <p style="margin:0 0 16px;">We received a request to reset your password. Click the button below to choose a new one:</p>
      <p style="margin:28px 0;text-align:center;">
        <a href="{reset_url}" style="display:inline-block;padding:14px 28px;border-radius:999px;background:#c19b54;color:#fff;text-decoration:none;font-weight:600;font-family:Arial,sans-serif;">Reset password</a>
      </p>
      <p style="margin:0 0 8px;font-size:14px;color:#9a8776;">Or paste this link into your browser:</p>
      <p style="margin:0 0 16px;font-size:13px;word-break:break-all;"><a href="{reset_url}" style="color:#c19b54;">{reset_url}</a></p>
      <p style="margin:0;font-size:14px;color:#9a8776;">This link expires in {expires_minutes} minutes. If you didn’t ask for a reset, ignore this email.</p>
    """
    return subject, _shell(
        title=subject,
        preheader="Reset your Chic A Boo password",
        body_html=body,
        site_url=site_url,
    )


def welcome_email(*, first_name: str | None, site_url: str) -> tuple[str, str]:
    name = (first_name or "friend").strip() or "friend"
    subject = "Welcome to Chic A Boo"
    body = f"""
      <h1 style="margin:0 0 12px;font-size:22px;color:#5c4a3a;font-family:Georgia,serif;">Hi {name}, you’re in</h1>
      <p style="margin:0 0 16px;">Your email is verified. Explore handcrafted crochet blooms, keepsakes, and gifts made with love.</p>
      <p style="margin:28px 0;text-align:center;">
        <a href="{site_url}" style="display:inline-block;padding:14px 28px;border-radius:999px;background:#c19b54;color:#fff;text-decoration:none;font-weight:600;font-family:Arial,sans-serif;">Start shopping</a>
      </p>
    """
    return subject, _shell(
        title=subject,
        preheader="Your Chic A Boo account is ready",
        body_html=body,
        site_url=site_url,
    )


def order_confirmation_email(
    *,
    order_number: str,
    total_label: str,
    site_url: str,
    track_url: str | None = None,
) -> tuple[str, str]:
    subject = f"Order confirmed — {order_number}"
    track = track_url or f"{site_url.rstrip('/')}/track-order"
    body = f"""
      <h1 style="margin:0 0 12px;font-size:22px;color:#5c4a3a;font-family:Georgia,serif;">Thank you for your order</h1>
      <p style="margin:0 0 12px;">We’ve received <strong>{order_number}</strong>. Total: <strong>{total_label}</strong>.</p>
      <p style="margin:0 0 16px;">We’ll email you again when it ships. You can track updates anytime:</p>
      <p style="margin:28px 0;text-align:center;">
        <a href="{track}" style="display:inline-block;padding:14px 28px;border-radius:999px;background:#c19b54;color:#fff;text-decoration:none;font-weight:600;font-family:Arial,sans-serif;">Track order</a>
      </p>
    """
    return subject, _shell(
        title=subject,
        preheader=f"Order {order_number} confirmed",
        body_html=body,
        site_url=site_url,
    )


def order_shipped_email(
    *,
    order_number: str,
    site_url: str,
    track_url: str | None = None,
) -> tuple[str, str]:
    subject = f"Your Chic A Boo order is on the way — {order_number}"
    track = track_url or f"{site_url.rstrip('/')}/track-order"
    body = f"""
      <h1 style="margin:0 0 12px;font-size:22px;color:#5c4a3a;font-family:Georgia,serif;">It’s on the way</h1>
      <p style="margin:0 0 16px;">Good news — <strong>{order_number}</strong> has shipped. Track your parcel here:</p>
      <p style="margin:28px 0;text-align:center;">
        <a href="{track}" style="display:inline-block;padding:14px 28px;border-radius:999px;background:#c19b54;color:#fff;text-decoration:none;font-weight:600;font-family:Arial,sans-serif;">Track shipment</a>
      </p>
    """
    return subject, _shell(
        title=subject,
        preheader=f"Order {order_number} shipped",
        body_html=body,
        site_url=site_url,
    )


def admin_alert_email(*, title: str, detail: str, site_url: str) -> tuple[str, str]:
    subject = f"[Chic A Boo Admin] {title}"
    body = f"""
      <h1 style="margin:0 0 12px;font-size:20px;color:#5c4a3a;font-family:Georgia,serif;">{title}</h1>
      <p style="margin:0;white-space:pre-wrap;">{detail}</p>
    """
    return subject, _shell(
        title=subject,
        preheader=title,
        body_html=body,
        site_url=site_url,
    )
