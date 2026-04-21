#!/usr/bin/env python3
"""
Infinity Tool: update Infinity queue settings via:
PUT https://{host}/api/core-config-service/v1/queues/{queueId}

Targets:
- single queue: --queue-id or --name
- bulk all queues: --all-queues (optional --folder-id to limit to that folder)
- batch file: --batch
- folder of queue JSON / id list: --queues-dir
- inspect live object: --dump-queue QUEUE_ID
- list queue folders (TSV): --list-folders (GET core/v4/folders/queues)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from infinity_client import InfinityClient

PACKAGE_ROOT = Path(__file__).resolve().parent


def load_properties(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise SystemExit(
            f"Properties file not found: {path}\n"
            f"Copy {PACKAGE_ROOT / 'properties.example.json'} to properties.json "
            "and set host, username, password."
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _deep_merge_dicts(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(a)
    for bk, bv in b.items():
        if bk in out and isinstance(out[bk], dict) and isinstance(bv, dict):
            out[bk] = _deep_merge_dicts(out[bk], bv)
        else:
            out[bk] = bv
    return out


def nest_dotted_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    """
    Turn dotted keys into nested JSON for the queue PUT body, e.g.:
    config.outboundCallerId -> {"config": {"outboundCallerId": ...}}
    config.interaction.HistoryTable -> {"config": {"interaction": {"HistoryTable": ...}}}

    Plain keys without dots are merged afterward; dict values merge with nested paths.
    """
    root: Dict[str, Any] = {}
    for k, v in settings.items():
        if "." not in k:
            continue
        parts = k.split(".")
        cur = root
        for part in parts[:-1]:
            nxt = cur.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                cur[part] = nxt
            cur = nxt
        cur[parts[-1]] = v
    for k, v in settings.items():
        if "." in k:
            continue
        if k in root and isinstance(root[k], dict) and isinstance(v, dict):
            root[k] = _deep_merge_dicts(root[k], v)
        else:
            root[k] = v
    return root


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


def queue_resource_url(api: InfinityClient, queue_id: str) -> str:
    return api.get_url() + f"core-config-service/v1/queues/{queue_id}"


def list_queues(api: InfinityClient) -> List[Dict[str, Any]]:
    api._ensure_token_valid()
    if not api.session:
        raise RuntimeError("Not logged in; call perform_oauth_login() first.")
    url = api.get_url() + "core/v4/queues"
    r = api.session.get(url)
    if r.status_code != 200:
        raise SystemExit(f"Failed to list queues: {r.status_code} {r.text}")
    data = r.json()
    queues = data.get("queues")
    if isinstance(queues, list):
        return queues
    return []


def print_queue_folders(api: InfinityClient, parent_folder_id: Optional[str]) -> None:
    """GET core/v4/folders/queues; print folders[] as TSV (id, parentFolderId, displayName)."""
    params: Optional[Dict[str, Any]] = None
    if parent_folder_id is not None:
        params = {"parentFolderId": parent_folder_id}
    data = api.list_queue_folders(params)
    if not isinstance(data, dict):
        raise SystemExit("Unexpected folders response (not a JSON object).")
    folders = data.get("folders")
    if not isinstance(folders, list):
        raise SystemExit("Response has no folders array from GET core/v4/folders/queues.")
    print("id\tparentFolderId\tdisplayName")
    for f in folders:
        if not isinstance(f, dict):
            continue
        fid = f.get("id") or ""
        p = f.get("parentFolderId")
        pid = "" if p is None else str(p)
        name = f.get("displayName") or f.get("name") or ""
        print(f"{fid}\t{pid}\t{name}")
    print(f"Total folders: {len(folders)}")


def print_queue_list(
    api: InfinityClient,
    *,
    folder_scope: Optional[str] = None,
    personal_user_only: bool = False,
) -> None:
    if personal_user_only:
        queues = list_personal_queues(api, folder_scope)
    else:
        all_queues = list_queues(api)
        queues = [
            q
            for q in all_queues
            if _queue_matches_filters(
                q,
                folder_scope=folder_scope,
                personal_user_only=False,
            )
        ]
    if not queues:
        print("No queues matched the requested filters.")
        return
    for q in queues:
        qid = q.get("queueId") or q.get("id") or ""
        name = q.get("name") or q.get("queueName") or ""
        print(f"{qid}\t{name}")
    print(f"Total queues: {len(queues)}")


def fetch_queue_record(api: InfinityClient, queue_id: str) -> Dict[str, Any]:
    api._ensure_token_valid()
    if not api.session:
        raise RuntimeError("Not logged in")
    url = queue_resource_url(api, queue_id)
    r = api.session.get(url)
    r.raise_for_status()
    data = r.json()
    q = data.get("queues")
    if isinstance(q, list) and q:
        return q[0]
    if isinstance(q, dict):
        return q
    if "queue" in data and isinstance(data["queue"], dict):
        return data["queue"]
    return data


def resolve_queue_id(
    api: InfinityClient,
    queue_id: Optional[str],
    name: Optional[str],
    folder_scope: Optional[str] = None,
    personal_user_only: bool = False,
) -> str:
    personal_ids: Optional[set[str]] = None
    personal_queues: Optional[List[Dict[str, Any]]] = None
    if personal_user_only:
        personal_queues = list_personal_queues(api, folder_scope)
        personal_ids = {
            str(q.get("queueId") or q.get("id"))
            for q in personal_queues
            if (q.get("queueId") or q.get("id"))
        }

    if queue_id:
        qid_str = str(queue_id).strip()
        if folder_scope is None and not personal_user_only:
            return qid_str
        if personal_ids is not None and qid_str not in personal_ids:
            raise SystemExit(f"Queue {qid_str} is not in personal queues for the requested scope.")
        if personal_user_only:
            return qid_str
        for q in list_queues(api):
            if str(q.get("queueId") or q.get("id")) != qid_str:
                continue
            if _queue_matches_filters(q, folder_scope=folder_scope, personal_user_only=personal_user_only):
                return qid_str
            qf = _queue_folder_id(q)
            details: List[str] = []
            if folder_scope is not None:
                exp = "root" if folder_scope == "" else repr(folder_scope)
                details.append(f"folderId={qf!r}, expected {exp}")
            if personal_user_only:
                details.append("expected personal user queue")
            detail = "; ".join(details) if details else "failed filter"
            raise SystemExit(f"Queue {qid_str} does not match requested scope ({detail}).")
        raise SystemExit(f"Queue id not found in core/v4/queues list: {qid_str}")
    if not name:
        raise SystemExit("Provide --queue-id or --name.")
    needle = name.strip().lower()
    if personal_user_only and personal_queues is not None:
        for q in personal_queues:
            qid = q.get("queueId") or q.get("id")
            qname = q.get("name") or q.get("queueName") or q.get("displayName")
            if qid and isinstance(qname, str) and qname.lower() == needle:
                return str(qid)

    for q in list_queues(api):
        qid = q.get("queueId") or q.get("id")
        qname = q.get("name") or q.get("queueName")
        if not qid or not isinstance(qname, str) or qname.lower() != needle:
            continue
        if personal_ids is not None and str(qid) not in personal_ids:
            continue
        if not _queue_matches_filters(q, folder_scope=folder_scope, personal_user_only=personal_user_only):
            continue
        return str(qid)
    hint_parts: List[str] = []
    if folder_scope is not None:
        hint_parts.append(f"in folder {folder_scope!r}")
    if personal_user_only:
        hint_parts.append("among personal user queues")
    hint = (" " + " ".join(hint_parts)) if hint_parts else ""
    raise SystemExit(f"No queue found with name: {name}{hint}")


def build_settings_payload(args: argparse.Namespace) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for item in args.set or []:
        if "=" not in item:
            raise SystemExit(f"--set expects KEY=VALUE, got: {item!r}")
        k, v = item.split("=", 1)
        k = k.strip()
        if not k:
            raise SystemExit(f"Invalid --set: {item!r}")
        out[k] = parse_value(v)
    if getattr(args, "outbound_caller_id", None) is not None:
        out["config.outboundCallerId"] = args.outbound_caller_id
    if getattr(args, "journey_tab", False):
        # tabsDefault must be a JSON array, not a dotted numeric key map.
        cfg = out.get("config")
        if not isinstance(cfg, dict):
            cfg = {}
        cfg["tabsDefault"] = ["journey"]
        out["config"] = cfg
    return out


def put_queue_settings(
    api: InfinityClient,
    queue_id: str,
    settings: Dict[str, Any],
    *,
    raise_on_error: bool = True,
) -> bool:
    if not settings:
        raise SystemExit(
            "No settings to apply (use --set KEY=VALUE, --outbound-caller-id, or --journey-tab)."
        )
    api._ensure_token_valid()
    if not api.session:
        raise RuntimeError("Not logged in")
    url = queue_resource_url(api, queue_id)
    nested = nest_dotted_settings(settings)
    payload = {"queueId": queue_id, **nested}
    r = api.session.put(url, json=payload)
    if r.status_code not in (200, 204):
        msg = f"Update failed {r.status_code}: {r.text}"
        if raise_on_error:
            raise SystemExit(msg)
        print(f"ERROR {queue_id}: {msg}")
        return False
    print(f"Updated queue {queue_id}: {json.dumps(nested, indent=2)}")
    return True


def list_all_queue_ids(api: InfinityClient) -> List[str]:
    ids: List[str] = []
    for q in list_queues(api):
        qid = q.get("queueId") or q.get("id")
        if qid:
            ids.append(str(qid))
    return ids


def _folder_scope_from_arg(folder_id: Optional[str]) -> Optional[str]:
    """
    None = no folder filter (all queues).
    '' = root only (queues with null/missing folderId).
    non-empty str = that folder id.
    """
    if folder_id is None:
        return None
    s = folder_id.strip()
    if s.lower() in ("", "null", "(root)", "root"):
        return ""
    return s


def _queue_folder_id(q: Dict[str, Any]) -> Optional[str]:
    v = q.get("folderId")
    if v is None or v == "":
        return None
    return str(v)


def _matches_folder_scope(q: Dict[str, Any], folder_scope: Optional[str]) -> bool:
    if folder_scope is None:
        return True
    qf = _queue_folder_id(q)
    if folder_scope == "":
        return qf is None
    return qf == folder_scope


def _is_personal_user_queue(q: Dict[str, Any]) -> bool:
    # Queue list payloads can vary by tenant/version; check common flags.
    truthy_keys = (
        "isPersonalQueue",
        "personalQueue",
        "isUserQueue",
        "userQueue",
        "isPersonal",
        "personal",
    )
    for k in truthy_keys:
        v = q.get(k)
        if isinstance(v, bool) and v:
            return True
        if isinstance(v, str) and v.strip().lower() in ("true", "1", "yes"):
            return True
    qtype = q.get("queueType") or q.get("type")
    if isinstance(qtype, str) and qtype.strip().lower() in ("personal", "user", "user_personal"):
        return True

    def _contains_personal_type(v: Any) -> bool:
        if isinstance(v, dict):
            for k, item in v.items():
                if (
                    isinstance(k, str)
                    and k.strip().lower() == "type"
                    and isinstance(item, str)
                    and item.strip().lower() == "personal"
                ):
                    return True
                if _contains_personal_type(item):
                    return True
        elif isinstance(v, list):
            return any(_contains_personal_type(item) for item in v)
        return False

    if _contains_personal_type(q):
        return True

    return False


def _queue_matches_filters(
    q: Dict[str, Any],
    *,
    folder_scope: Optional[str],
    personal_user_only: bool,
) -> bool:
    if not _matches_folder_scope(q, folder_scope):
        return False
    if personal_user_only and not _is_personal_user_queue(q):
        return False
    return True


def _queue_ids_from_folders_response(data: Any) -> List[str]:
    if not isinstance(data, dict):
        return []
    queues = data.get("queues")
    if not isinstance(queues, list):
        return []
    ids: List[str] = []
    for q in queues:
        if not isinstance(q, dict):
            continue
        qid = q.get("queueId") or q.get("id")
        if qid:
            ids.append(str(qid))
    return ids


def _queues_from_folders_response(data: Any) -> List[Dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    queues = data.get("queues")
    if not isinstance(queues, list):
        return []
    return [q for q in queues if isinstance(q, dict)]


def list_queue_ids_in_folder(api: InfinityClient, folder_scope: str) -> List[str]:
    """
    Queue ids under a folder view from GET core/v4/folders/queues (parentFolderId query).
    folder_scope '' = root (parentFolderId null in query).
    """
    params: Dict[str, Any] = {"parentFolderId": "null" if folder_scope == "" else folder_scope}
    data = api.list_queue_folders(params)
    ids = _queue_ids_from_folders_response(data)
    if ids:
        return ids
    # Fallback: filter flat core/v4/queues list
    out: List[str] = []
    for q in list_queues(api):
        qid = q.get("queueId") or q.get("id")
        if not qid:
            continue
        qf = _queue_folder_id(q)
        if folder_scope == "":
            if qf is None:
                out.append(str(qid))
        else:
            if qf == folder_scope:
                out.append(str(qid))
    return out


def list_personal_queues(api: InfinityClient, folder_scope: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Personal queues from GET core/v4/folders/queues?type=personal.
    When folder_scope is set, constrain by parentFolderId.
    """
    params: Dict[str, Any] = {"type": "personal"}
    if folder_scope is not None:
        params["parentFolderId"] = "null" if folder_scope == "" else folder_scope
    data = api.list_queue_folders(params)
    queues = _queues_from_folders_response(data)
    if queues:
        return queues
    # Fallback if folders endpoint shape changes.
    out: List[Dict[str, Any]] = []
    for q in list_queues(api):
        qid = q.get("queueId") or q.get("id")
        if not qid:
            continue
        if not _queue_matches_filters(
            q,
            folder_scope=folder_scope,
            personal_user_only=True,
        ):
            continue
        out.append(q)
    return out


def list_personal_queue_ids(api: InfinityClient, folder_scope: Optional[str] = None) -> List[str]:
    out: List[str] = []
    for q in list_personal_queues(api, folder_scope):
        qid = q.get("queueId") or q.get("id")
        if qid:
            out.append(str(qid))
    return out


def apply_targets(
    api: InfinityClient,
    defaults: Dict[str, Any],
    targets: List[Dict[str, Any]],
    *,
    dry_run: bool,
    continue_on_error: bool = False,
    folder_scope: Optional[str] = None,
    personal_user_only: bool = False,
) -> None:
    ok = 0
    failed = 0
    for t in targets:
        qid = t.get("queueId") or t.get("id")
        name = t.get("name") or t.get("queueName")
        if not qid and name:
            qid = resolve_queue_id(
                api, None, str(name), folder_scope, personal_user_only=personal_user_only
            )
        if not qid:
            raise SystemExit(f"Each target needs queueId/id or name: {t!r}")
        if folder_scope is not None or personal_user_only:
            qid = resolve_queue_id(
                api, str(qid), None, folder_scope, personal_user_only=personal_user_only
            )

        settings = dict(defaults)
        for k, v in t.items():
            if k in ("queueId", "id", "name", "queueName"):
                continue
            if isinstance(k, str) and (
                k.startswith("attributes.")
                or k.startswith("permissions.")
                or k.startswith("settings.")
                or k.startswith("config.")
            ):
                settings[k] = v

        if dry_run:
            print(f"[dry-run] {qid}: {json.dumps(nest_dotted_settings(settings), indent=2)}")
            ok += 1
        else:
            if put_queue_settings(api, str(qid), settings, raise_on_error=not continue_on_error):
                ok += 1
            else:
                failed += 1
    print(f"Targets complete: {ok} succeeded, {failed} failed, {len(targets)} targets total.")


def _merge_queue_document(
    doc: Any,
    merged_defaults: Dict[str, Any],
    merged_targets: List[Dict[str, Any]],
    source: Path,
) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    if isinstance(doc, list):
        for i, item in enumerate(doc):
            if isinstance(item, str):
                merged_targets.append({"queueId": item})
            elif isinstance(item, dict):
                merged_targets.append(item)
            else:
                raise SystemExit(f"Invalid list item in {source}[{i}]: {item!r}")
        return merged_defaults, merged_targets
    if isinstance(doc, dict):
        if "targets" in doc:
            merged_defaults = {**merged_defaults, **(doc.get("defaults") or {})}
            merged_targets.extend(doc["targets"])
            return merged_defaults, merged_targets
        if "queueIds" in doc:
            merged_defaults = {**merged_defaults, **(doc.get("defaults") or {})}
            for qid in doc["queueIds"]:
                merged_targets.append({"queueId": str(qid)})
            return merged_defaults, merged_targets
        if set(doc.keys()) <= {"defaults"}:
            merged_defaults = {**merged_defaults, **(doc.get("defaults") or {})}
            return merged_defaults, merged_targets
        merged_targets.append(doc)
        return merged_defaults, merged_targets
    raise SystemExit(f"Invalid JSON root in {source}: expected object or array")


def load_queue_targets_from_directory(dir_path: Path) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    if not dir_path.is_dir():
        raise SystemExit(f"Not a directory: {dir_path}")
    json_files = sorted(dir_path.glob("*.json"))
    txt_path: Optional[Path] = None
    for name in ("queues.txt", "queue_ids.txt"):
        p = dir_path / name
        if p.is_file():
            txt_path = p
            break
    if not json_files and not txt_path:
        raise SystemExit(
            f"No queue inputs in {dir_path}: add one or more *.json files and/or queues.txt (or queue_ids.txt)."
        )
    merged_defaults: Dict[str, Any] = {}
    merged_targets: List[Dict[str, Any]] = []
    for path in json_files:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        merged_defaults, merged_targets = _merge_queue_document(doc, merged_defaults, merged_targets, path)
    if txt_path:
        for line in txt_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            merged_targets.append({"queueId": line})
    if not merged_targets:
        raise SystemExit(f"No queue targets found under {dir_path}.")
    return merged_defaults, merged_targets


def run_all_queues(
    api: InfinityClient,
    settings: Dict[str, Any],
    *,
    dry_run: bool,
    continue_on_error: bool,
    folder_scope: Optional[str] = None,
    personal_user_only: bool = False,
) -> None:
    if personal_user_only:
        queue_ids = list_personal_queue_ids(api, folder_scope)
    elif folder_scope is None:
        queue_ids = list_all_queue_ids(api)
    else:
        queue_ids = list_queue_ids_in_folder(api, folder_scope)
    if not queue_ids:
        if personal_user_only and folder_scope is not None:
            where = "among personal user queues in the selected folder"
        elif personal_user_only:
            where = "among personal user queues"
        elif folder_scope is not None:
            where = "in the selected folder"
        else:
            where = "from core/v4/queues"
        raise SystemExit(f"No queues {where}.")
    ok = 0
    failed = 0
    for qid in queue_ids:
        if dry_run:
            print(f"[dry-run] {qid} {json.dumps(nest_dotted_settings(settings))}")
            ok += 1
        else:
            if put_queue_settings(api, qid, settings, raise_on_error=not continue_on_error):
                ok += 1
            else:
                failed += 1
    print(f"Bulk complete: {ok} succeeded, {failed} failed, {len(queue_ids)} queues total.")


def run_batch(
    api: InfinityClient,
    batch_path: Path,
    dry_run: bool,
    *,
    folder_scope: Optional[str] = None,
    personal_user_only: bool = False,
) -> None:
    with open(batch_path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    defaults = doc.get("defaults") or {}
    targets: List[Dict[str, Any]] = doc.get("targets") or []
    if not targets:
        raise SystemExit("Batch file must contain a non-empty 'targets' array.")
    apply_targets(
        api,
        defaults,
        targets,
        dry_run=dry_run,
        continue_on_error=False,
        folder_scope=folder_scope,
        personal_user_only=personal_user_only,
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Infinity Tool: update Infinity queue settings via core-config-service.")
    p.add_argument(
        "--properties",
        type=Path,
        default=PACKAGE_ROOT / "properties.json",
        help="Path to properties.json (infinity host, username, password).",
    )
    p.add_argument(
        "--all-queues",
        action="store_true",
        help="Fetch all queues from core/v4/queues and apply the same settings to each (bulk).",
    )
    p.add_argument(
        "--stop-on-first-error",
        action="store_true",
        help="With --all-queues or --queues-dir: exit on first failed PUT (default: log and continue).",
    )
    p.add_argument("--queue-id", help="Infinity queue id.")
    p.add_argument("--name", help="Resolve queue id from core/v4/queues list by exact name.")
    p.add_argument(
        "--set",
        action="append",
        metavar="KEY=VALUE",
        help='Any flat queue PUT field (repeatable), e.g. config.outboundCallerId="+17189355100".',
    )
    p.add_argument(
        "--outbound-caller-id",
        metavar="E164",
        dest="outbound_caller_id",
        help='Convenience: sets config.outboundCallerId (e.g. "+17189355100").',
    )
    p.add_argument(
        "--journey-tab",
        action="store_true",
        dest="journey_tab",
        help='Convenience: sets config.tabsDefault to ["journey"].',
    )
    p.add_argument("--batch", type=Path, help="JSON file with defaults + targets.")
    p.add_argument(
        "--queues-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="Folder of queue inputs: any *.json (batch format, array of ids/targets, or one target object) "
        "and/or queues.txt or queue_ids.txt (one queue id per line, # comments allowed).",
    )
    p.add_argument("--dump-queue", metavar="QUEUE_ID", help="Print queue JSON from GET (discover keys).")
    p.add_argument("--list-queues", action="store_true", help="List queues from core/v4/queues and exit.")
    p.add_argument(
        "--list-folders",
        action="store_true",
        help="GET core/v4/folders/queues and print folders[] as TSV (id, parentFolderId, displayName).",
    )
    p.add_argument(
        "--parent-folder-id",
        default=None,
        metavar="ID",
        help="Only with --list-folders: set parentFolderId query (default is null).",
    )
    p.add_argument(
        "--folder-id",
        default=None,
        metavar="ID",
        help="Restrict updates to queues in this folder (id from --list-folders). "
        "Use 'root' for queues with no folder. With --all-queues: only those queues; "
        "with --name/--queue-id, --batch, or --queues-dir: match or validate folder.",
    )
    p.add_argument(
        "--personal-user-queues",
        action="store_true",
        help="Restrict targets to personal/user queues only. Works with --all-queues and can be combined "
        "with --folder-id; for --queue-id/--name, --batch, and --queues-dir it validates each target.",
    )
    p.add_argument("--dry-run", action="store_true", help="Print payload only; no PUT.")

    args = p.parse_args()
    folder_scope = _folder_scope_from_arg(args.folder_id)
    if args.batch and args.queues_dir:
        raise SystemExit("Use either --batch or --queues-dir, not both.")
    if args.all_queues and args.queues_dir:
        raise SystemExit("Use either --all-queues or --queues-dir, not both.")

    cfg = load_properties(args.properties)
    inf = cfg["infinity"]
    api = InfinityClient(host=inf["host"], username=inf["username"], password=inf["password"])
    api.perform_oauth_login()

    if args.dump_queue:
        rec = fetch_queue_record(api, args.dump_queue)
        print(json.dumps(rec, indent=2))
        return

    if args.list_queues:
        print_queue_list(
            api,
            folder_scope=folder_scope,
            personal_user_only=args.personal_user_queues,
        )
        return

    if args.list_folders:
        print_queue_folders(api, args.parent_folder_id)
        return

    if args.batch:
        run_batch(
            api,
            args.batch,
            args.dry_run,
            folder_scope=folder_scope,
            personal_user_only=args.personal_user_queues,
        )
        return

    if args.queues_dir:
        file_defaults, targets = load_queue_targets_from_directory(args.queues_dir)
        cmd_defaults = build_settings_payload(args)
        merged_defaults = {**file_defaults, **cmd_defaults}
        apply_targets(
            api,
            merged_defaults,
            targets,
            dry_run=args.dry_run,
            continue_on_error=not args.stop_on_first_error,
            folder_scope=folder_scope,
            personal_user_only=args.personal_user_queues,
        )
        return

    settings = build_settings_payload(args)

    if args.all_queues:
        if not settings:
            raise SystemExit(
                "With --all-queues, provide at least one --set KEY=VALUE, "
                "--outbound-caller-id, or --journey-tab."
            )
        run_all_queues(
            api,
            settings,
            dry_run=args.dry_run,
            continue_on_error=not args.stop_on_first_error,
            folder_scope=folder_scope,
            personal_user_only=args.personal_user_queues,
        )
        return

    if args.queue_id or args.name:
        queue_id = resolve_queue_id(
            api,
            args.queue_id,
            args.name,
            folder_scope,
            personal_user_only=args.personal_user_queues,
        )
        if args.dry_run:
            print(json.dumps({"queueId": queue_id, **nest_dotted_settings(settings)}, indent=2))
            return
        put_queue_settings(api, queue_id, settings, raise_on_error=True)
        return

    raise SystemExit(
        "Specify a target: --list-queues, --list-folders, --all-queues, --queues-dir, "
        "--queue-id / --name, --batch, or --dump-queue."
    )


if __name__ == "__main__":
    main()
