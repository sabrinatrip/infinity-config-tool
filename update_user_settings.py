#!/usr/bin/env python3
# Windows: run with  py -3 update_user_settings.py   or   run_update_user_settings.bat
"""
Infinity Tool: update Infinity user settings (e.g. ring time, max missed interaction count) via
core-config-service PUT /v1/users/{userId}.

Use --all-users to list everyone from core/v4/users and apply the same settings
in bulk. For a single user, use --user-id or --email.

Credentials: copy properties.example.json to properties.json and fill infinity.*,
or pass --properties /path/to/file.json.

Attribute key names vary by tenant/version; use --dump-user to inspect the live
user object, or capture the JSON body when saving in Admin UI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Windows: avoid UnicodeEncodeError in some consoles when printing JSON
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
from typing import Any, Dict, List, Optional

from infinity_client import InfinityClient

PACKAGE_ROOT = Path(__file__).resolve().parent

ENV_RING_KEY = "INFINITY_ATTR_RING_TIME"
ENV_MISSED_KEY = "INFINITY_ATTR_MAX_MISSED"


def default_ring_time_key() -> str:
    return os.environ.get(ENV_RING_KEY, "attributes.agent.ringTime")


def default_max_missed_key() -> str:
    return os.environ.get(ENV_MISSED_KEY, "attributes.agent.maxMissedInteractions")


def load_properties(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise SystemExit(
            f"Properties file not found: {path}\n"
            f"Copy {PACKAGE_ROOT / 'properties.example.json'} to properties.json "
            "and set host, username, password."
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_value(raw: str) -> Any:
    s = raw.strip()
    low = s.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        if s.startswith("-") and s[1:].isdigit():
            return int(s)
        if s.isdigit():
            return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return s


def fetch_user_record(api: InfinityClient, user_id: str) -> Dict[str, Any]:
    api._ensure_token_valid()
    if not api.session:
        raise RuntimeError("Not logged in")
    url = api.get_url() + f"core-config-service/v1/users/{user_id}"
    r = api.session.get(url)
    r.raise_for_status()
    data = r.json()
    u = data.get("users")
    if isinstance(u, list) and u:
        return u[0]
    if isinstance(u, dict):
        return u
    if "user" in data and isinstance(data["user"], dict):
        return data["user"]
    return data


def resolve_user_id(api: InfinityClient, user_id: Optional[str], email: Optional[str]) -> str:
    if user_id:
        return user_id
    if not email:
        raise SystemExit("Provide --user-id or --email.")
    api._ensure_token_valid()
    users = api.list_users()
    em = email.strip().lower()
    for u in users or []:
        if (u.get("email") or "").lower() == em:
            uid = u.get("userId") or u.get("id")
            if uid:
                return uid
    raise SystemExit(f"No user found with email: {email}")


def build_settings_payload(args: argparse.Namespace) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if args.ring_time is not None:
        out[args.ring_time_key] = args.ring_time
    if args.max_missed is not None:
        out[args.max_missed_key] = args.max_missed
    for item in args.set or []:
        if "=" not in item:
            raise SystemExit(f"--set expects KEY=VALUE, got: {item!r}")
        k, v = item.split("=", 1)
        k = k.strip()
        if not k:
            raise SystemExit(f"Invalid --set: {item!r}")
        out[k] = parse_value(v)
    return out


def put_user_settings(
    api: InfinityClient,
    user_id: str,
    settings: Dict[str, Any],
    *,
    raise_on_error: bool = True,
) -> bool:
    if not settings:
        raise SystemExit("No settings to apply (use --ring-time, --max-missed, and/or --set).")
    api._ensure_token_valid()
    if not api.session:
        raise RuntimeError("Not logged in")
    url = api.get_url() + f"core-config-service/v1/users/{user_id}"
    payload = {"userId": user_id, **settings}
    r = api.session.put(url, json=payload)
    if r.status_code not in (200, 204):
        msg = f"Update failed {r.status_code}: {r.text}"
        if raise_on_error:
            raise SystemExit(msg)
        print(f"ERROR {user_id}: {msg}", file=sys.stderr)
        return False
    print(f"Updated user {user_id}: {json.dumps(settings, indent=2)}")
    return True


def list_all_user_ids(api: InfinityClient) -> List[str]:
    api._ensure_token_valid()
    users = api.list_users() or []
    ids: List[str] = []
    for u in users:
        uid = u.get("userId") or u.get("id")
        if uid:
            ids.append(str(uid))
    return ids


def run_all_users(
    api: InfinityClient,
    settings: Dict[str, Any],
    *,
    dry_run: bool,
    continue_on_error: bool,
) -> None:
    user_ids = list_all_user_ids(api)
    if not user_ids:
        raise SystemExit("No users returned from core/v4/users (list_users).")
    ok = 0
    failed = 0
    for uid in user_ids:
        if dry_run:
            print(f"[dry-run] {uid} {json.dumps(settings)}")
            ok += 1
        else:
            if put_user_settings(api, uid, settings, raise_on_error=not continue_on_error):
                ok += 1
            else:
                failed += 1
    print(f"Bulk complete: {ok} succeeded, {failed} failed, {len(user_ids)} users total.")


def run_batch(
    api: InfinityClient,
    batch_path: Path,
    dry_run: bool,
    default_ring_key: str,
    default_missed_key: str,
) -> None:
    with open(batch_path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    defaults = doc.get("defaults") or {}
    targets: List[Dict[str, Any]] = doc.get("targets") or []
    if not targets:
        raise SystemExit("Batch file must contain a non-empty 'targets' array.")

    for t in targets:
        uid = t.get("userId") or t.get("id")
        email = t.get("email")
        if not uid and email:
            uid = resolve_user_id(api, None, email)
        if not uid:
            raise SystemExit(f"Each target needs userId or email: {t!r}")

        settings = dict(defaults)
        if "ring_time" in t:
            settings[default_ring_key] = t["ring_time"]
        if "max_missed" in t or "max_missed_interactions" in t:
            v = t.get("max_missed", t.get("max_missed_interactions"))
            settings[default_missed_key] = v
        for k, v in t.items():
            if k in ("userId", "id", "email", "ring_time", "max_missed", "max_missed_interactions"):
                continue
            if isinstance(k, str) and (k.startswith("attributes.") or k.startswith("permissions.")):
                settings[k] = v

        if dry_run:
            print(f"[dry-run] {uid}: {json.dumps(settings, indent=2)}")
        else:
            put_user_settings(api, uid, settings, raise_on_error=True)


def main() -> None:
    p = argparse.ArgumentParser(description="Infinity Tool: update Infinity user settings via core-config-service.")
    p.add_argument(
        "--properties",
        type=Path,
        default=PACKAGE_ROOT / "properties.json",
        help="Path to properties.json (infinity host, username, password).",
    )
    p.add_argument(
        "--all-users",
        action="store_true",
        help="Fetch all users from core/v4/users and apply the same settings to each (bulk).",
    )
    p.add_argument(
        "--stop-on-first-error",
        action="store_true",
        help="With --all-users: exit on first failed PUT (default: log and continue).",
    )
    p.add_argument("--user-id", help="Infinity user id (e.g. 002d0111...).")
    p.add_argument("--email", help="Resolve user id from core/v4/users list.")
    p.add_argument("--ring-time", type=int, metavar="SECONDS", help="Agent ring time (uses ring-time attr key).")
    p.add_argument("--max-missed", type=int, metavar="N", help="Max missed interactions (uses max-missed attr key).")
    p.add_argument(
        "--ring-time-key",
        default=None,
        help=f"API key for ring time (default: env {ENV_RING_KEY} or attributes.agent.ringTime).",
    )
    p.add_argument(
        "--max-missed-key",
        default=None,
        help=f"API key for max missed (default: env {ENV_MISSED_KEY} or attributes.agent.maxMissedInteractions).",
    )
    p.add_argument(
        "--set",
        action="append",
        metavar="KEY=VALUE",
        help="Any flat user PUT field, e.g. attributes.agent.someFlag=true (repeatable).",
    )
    p.add_argument("--batch", type=Path, help="JSON file with defaults + targets.")
    p.add_argument("--dump-user", metavar="USER_ID", help="Print user JSON from GET (discover attribute keys).")
    p.add_argument("--dry-run", action="store_true", help="Print payload only; no PUT.")

    args = p.parse_args()
    args.ring_time_key = args.ring_time_key or default_ring_time_key()
    args.max_missed_key = args.max_missed_key or default_max_missed_key()

    cfg = load_properties(args.properties)
    inf = cfg["infinity"]
    api = InfinityClient(host=inf["host"], username=inf["username"], password=inf["password"])
    api.perform_oauth_login()

    if args.dump_user:
        rec = fetch_user_record(api, args.dump_user)
        print(json.dumps(rec, indent=2))
        return

    if args.batch:
        run_batch(api, args.batch, args.dry_run, args.ring_time_key, args.max_missed_key)
        return

    settings = build_settings_payload(args)

    if args.all_users:
        if not settings:
            raise SystemExit(
                "With --all-users, provide at least one of --ring-time, --max-missed, or --set."
            )
        run_all_users(
            api,
            settings,
            dry_run=args.dry_run,
            continue_on_error=not args.stop_on_first_error,
        )
        return

    if args.user_id or args.email:
        user_id = resolve_user_id(api, args.user_id, args.email)
        if args.dry_run:
            print(json.dumps({"userId": user_id, **settings}, indent=2))
            return
        put_user_settings(api, user_id, settings, raise_on_error=True)
        return

    raise SystemExit(
        "Specify a target: --all-users, or --user-id / --email, or --batch, or --dump-user."
    )


if __name__ == "__main__":
    main()
