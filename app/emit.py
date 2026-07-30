"""Publish the daily site to S3, and sync the append-only history/ state across runs.

Port of the Lambda-used parts of src/emit.ts (upload_site_dir) plus the history pull/push that was
sync_history.py — folded here so the handler has one S3 helper module. boto3 is provided by the
Lambda runtime.

The report's port-frontend.py produces a COMPLETE site dir (index.html + engine.js + boot.js +
data/*.json); this uploads it verbatim. The site is regenerated every run, so the shell always
matches the data (build.py renders anchor-day fragments into index.html — a frozen shell goes stale).
"""
from __future__ import annotations

import gzip
import os
import re
from typing import Dict, List, Tuple

CONTENT_TYPE: Dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}

# boto3 is imported lazily so environments without AWS (the DeployBay container) can import this
# module without boto3 installed — only the S3 functions below actually need it.
_s3_client = None


def _s3():
    global _s3_client
    if _s3_client is None:
        import boto3
        _s3_client = boto3.client("s3")
    return _s3_client


def parse_s3(s3url: str) -> Tuple[str, str]:
    m = re.match(r"^s3://([^/]+)/?(.*)$", s3url)
    if not m:
        raise ValueError(f"bad s3 url: {s3url}")
    bucket, key = m.group(1), m.group(2)
    prefix = (key.rstrip("/") + "/") if key else ""
    return bucket, prefix


def upload_site_dir(dir_path: str, s3url: str, gz: bool = True) -> int:
    """Publish a complete site dir to S3. Returns the number of objects written."""
    bucket, prefix = parse_s3(s3url)
    count = 0
    for root, _dirs, files in os.walk(dir_path):
        for name in files:
            if name.endswith(".gz"):
                continue  # upload raw; ContentEncoding handles gzip
            path = os.path.join(root, name)
            rel = os.path.relpath(path, dir_path).replace(os.sep, "/")
            with open(path, "rb") as f:
                body = f.read()
            ext = os.path.splitext(name)[1]
            extra = {
                "ContentType": CONTENT_TYPE.get(ext, "application/octet-stream"),
                # index.html must revalidate each load (it changes daily); data can cache briefly.
                "CacheControl": "no-cache" if name == "index.html" else "public, max-age=300",
            }
            if gz:
                body = gzip.compress(body)
                extra["ContentEncoding"] = "gzip"
            _s3().put_object(Bucket=bucket, Key=prefix + rel, Body=body, **extra)
            count += 1
    return count


def history_pull(s3url: str, dest_dir: str) -> int:
    """Restore the append-only history/ state from S3 into dest_dir. Returns files restored."""
    bucket, prefix = parse_s3(s3url)
    os.makedirs(dest_dir, exist_ok=True)
    count = 0
    paginator = _s3().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []) or []:
            key = obj["Key"]
            rel = key[len(prefix):]
            if not rel or rel.endswith("/"):
                continue
            out = os.path.join(dest_dir, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            _s3().download_file(bucket, key, out)
            count += 1
    return count


def history_push(src_dir: str, s3url: str) -> int:
    """Persist the updated history/ dir back to S3. Returns files pushed."""
    bucket, prefix = parse_s3(s3url)
    count = 0
    for root, _dirs, files in os.walk(src_dir):
        for name in files:
            path = os.path.join(root, name)
            rel = os.path.relpath(path, src_dir).replace(os.sep, "/")
            _s3().upload_file(path, bucket, prefix + rel)
            count += 1
    return count
