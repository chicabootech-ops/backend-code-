#!/usr/bin/env python3
"""Reconcile ops.notification_templates against the templates Meta actually has.

    python scripts/whatsapp_templates.py            # report only
    python scripts/whatsapp_templates.py --apply    # fix language, deactivate missing

Needs WHATSAPP_ACCESS_TOKEN, WHATSAPP_BUSINESS_ACCOUNT_ID and DATABASE_URL.
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.request
import json

import psycopg2

API_VERSION = os.environ.get("WHATSAPP_API_VERSION", "v21.0")


def die(message: str) -> None:
    raise SystemExit(f"error: {message}")


def fetch_meta_templates(waba_id: str, token: str) -> list[dict]:
    url = (
        f"https://graph.facebook.com/{API_VERSION}/{waba_id}/message_templates"
        f"?fields=name,language,status,category&limit=200"
    )
    out: list[dict] = []
    while url:
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.load(response)
        out.extend(body.get("data", []))
        url = (body.get("paging") or {}).get("next")
    return out


def db_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        die("DATABASE_URL is required")
    return url.replace("postgresql+asyncpg://", "postgresql://").split("?")[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    token = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
    waba_id = os.environ.get("WHATSAPP_BUSINESS_ACCOUNT_ID", "")
    if not token or not waba_id:
        die("WHATSAPP_ACCESS_TOKEN and WHATSAPP_BUSINESS_ACCOUNT_ID are required")

    meta = fetch_meta_templates(waba_id, token)
    approved = {
        (t["name"], t["language"]) for t in meta if t.get("status") == "APPROVED"
    }
    by_name: dict[str, list[dict]] = {}
    for t in meta:
        by_name.setdefault(t["name"], []).append(t)

    print(f"Meta returned {len(meta)} templates ({len(approved)} approved)\n")
    for t in sorted(meta, key=lambda x: (x["name"], x["language"])):
        print(f"  {t['status']:<10} {t['name']:<34} {t['language']:<7} {t.get('category','')}")
    print()

    with psycopg2.connect(db_url()) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, notification_type, provider_template_name, language
                 FROM ops.notification_templates
                WHERE channel='whatsapp' AND provider='whatsapp' AND is_active
                ORDER BY notification_type;"""
        )
        rows = cur.fetchall()

        ok, relang, missing = [], [], []
        for row_id, ntype, name, lang in rows:
            if (name, lang) in approved:
                ok.append((ntype, name, lang))
            elif name in by_name:
                alt = [t for t in by_name[name] if t.get("status") == "APPROVED"]
                if alt:
                    relang.append((row_id, ntype, name, lang, alt[0]["language"]))
                else:
                    missing.append((row_id, ntype, name, lang, by_name[name][0]["status"]))
            else:
                missing.append((row_id, ntype, name, lang, "NOT IN META"))

        print(f"OK                    : {len(ok)}")
        print(f"Wrong language        : {len(relang)}")
        for _, ntype, name, lang, real in relang:
            print(f"    {ntype:<24} {name:<34} {lang} -> {real}")
        print(f"Missing / unapproved  : {len(missing)}")
        for _, ntype, name, lang, why in missing:
            print(f"    {ntype:<24} {name:<34} {lang}  [{why}]")

        if not args.apply:
            print("\nreport only — re-run with --apply to fix")
            return

        for row_id, _, _, _, real in relang:
            cur.execute(
                "UPDATE ops.notification_templates SET language=%s WHERE id=%s;",
                (real, row_id),
            )
        for row_id, *_ in missing:
            cur.execute(
                "UPDATE ops.notification_templates SET is_active=FALSE WHERE id=%s;",
                (row_id,),
            )
        conn.commit()
        print(f"\napplied: {len(relang)} relanguaged, {len(missing)} deactivated")


if __name__ == "__main__":
    main()
