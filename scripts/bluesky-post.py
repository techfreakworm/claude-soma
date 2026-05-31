#!/usr/bin/env python3
"""scripts/bluesky-post.py -- AT Protocol post helper called by social-bluesky-poster.

Usage:
    python3 scripts/bluesky-post.py \
        --creds ~/.claude-pw/bluesky.json \
        --text "Post text here" \
        [--reply-to-uri at://did:plc:xxx/app.bsky.feed.post/yyy] \
        [--reply-to-cid bafyrei...] \
        [--image-path /abs/path/img.png --image-alt "Alt text"]

    --image-path / --image-alt may be repeated up to 4 times (one per image).

Outputs a single JSON line on stdout:
    {"uri": "at://...", "cid": "bafyrei..."}

Exit 0 on success; non-zero + error on stderr on failure.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

BSKY_HOST = os.environ.get("BSKY_HOST", "https://bsky.social")
XRPC = f"{BSKY_HOST}/xrpc"


def _http_post(url: str, payload: dict, headers: dict | None = None) -> dict:
    body = json.dumps(payload).encode()
    req_headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=body, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        raise RuntimeError(
            f"HTTP {exc.code} from {url}: {raw[:500]}"
        ) from exc


def _http_post_bytes(url: str, body: bytes, content_type: str, headers: dict | None = None) -> dict:
    req_headers = {"Content-Type": content_type, "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=body, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        raise RuntimeError(
            f"HTTP {exc.code} from {url}: {raw[:500]}"
        ) from exc


def create_session(identifier: str, app_password: str) -> str:
    resp = _http_post(
        f"{XRPC}/com.atproto.server.createSession",
        {"identifier": identifier, "password": app_password},
    )
    token = resp.get("accessJwt")
    if not token:
        raise RuntimeError(f"createSession returned no accessJwt: {resp}")
    return token


def upload_blob(path: str, token: str) -> dict:
    data = Path(path).read_bytes()
    mime, _ = mimetypes.guess_type(path)
    if not mime:
        mime = "application/octet-stream"
    resp = _http_post_bytes(
        f"{XRPC}/com.atproto.repo.uploadBlob",
        data,
        mime,
        {"Authorization": f"Bearer {token}"},
    )
    return resp["blob"]


def make_post(
    text: str,
    token: str,
    reply_uri: str | None,
    reply_cid: str | None,
    image_paths: list[str],
    image_alts: list[str],
) -> dict:
    record: dict = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": _utc_now(),
    }

    if reply_uri and reply_cid:
        ref = {"uri": reply_uri, "cid": reply_cid}
        record["reply"] = {"root": ref, "parent": ref}

    if image_paths:
        images = []
        for img_path, alt in zip(image_paths, image_alts):
            blob = upload_blob(img_path, token)
            images.append({"alt": alt, "image": blob})
        record["embed"] = {
            "$type": "app.bsky.embed.images",
            "images": images,
        }

    resp = _http_post(
        f"{XRPC}/com.atproto.repo.createRecord",
        {
            "repo": _did_from_token(token),
            "collection": "app.bsky.feed.post",
            "record": record,
        },
        {"Authorization": f"Bearer {token}"},
    )
    return {"uri": resp["uri"], "cid": resp["cid"]}


def _utc_now() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _did_from_token(token: str) -> str:
    import base64
    parts = token.split(".")
    if len(parts) < 2:
        raise RuntimeError("Cannot parse DID from token: unexpected JWT format")
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded))
    did = payload.get("sub") or payload.get("did")
    if not did:
        raise RuntimeError(f"No DID (sub/did) in JWT payload: {list(payload.keys())}")
    return did


def main() -> None:
    parser = argparse.ArgumentParser(description="Post to Bluesky via AT Protocol.")
    parser.add_argument("--creds", default=os.path.expanduser("~/.claude-pw/bluesky.json"))
    parser.add_argument("--text", required=True)
    parser.add_argument("--reply-to-uri", default=None)
    parser.add_argument("--reply-to-cid", default=None)
    parser.add_argument("--image-path", action="append", default=[], dest="image_paths")
    parser.add_argument("--image-alt", action="append", default=[], dest="image_alts")
    args = parser.parse_args()

    if len(args.image_paths) > 4:
        print("ERROR: Bluesky supports at most 4 images per post.", file=sys.stderr)
        sys.exit(1)
    if len(args.image_paths) != len(args.image_alts):
        print("ERROR: --image-path and --image-alt must be paired (same count).", file=sys.stderr)
        sys.exit(1)

    creds_path = Path(args.creds).expanduser()
    if not creds_path.exists():
        print(
            f"ERROR: credentials not found at {creds_path} -- run scripts/bluesky-login.sh first.",
            file=sys.stderr,
        )
        sys.exit(1)

    creds = json.loads(creds_path.read_text())
    if "encrypted" in creds:
        print(
            "ERROR: credentials are encrypted -- run scripts/pw-decrypt.sh bluesky first.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        token = create_session(creds["identifier"], creds["app_password"])
        result = make_post(
            args.text,
            token,
            args.reply_to_uri,
            args.reply_to_cid,
            args.image_paths,
            args.image_alts,
        )
        print(json.dumps(result))
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
