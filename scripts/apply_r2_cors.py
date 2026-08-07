"""Apply Cloudflare R2 bucket CORS for Pages / custom domain uploads."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"

CORS_RULES = [
    {
        "AllowedOrigins": [
            "https://dubbyai.com",
            "https://www.dubbyai.com",
            "https://creator1008.github.io",
            "http://localhost:3000",
            "http://localhost:3001",
            "https://localhost",
            "http://localhost",
            "capacitor://localhost",
        ],
        "AllowedMethods": ["GET", "PUT", "HEAD"],
        "AllowedHeaders": ["*"],
        "ExposeHeaders": ["ETag", "etag"],
        "MaxAgeSeconds": 3600,
    }
]


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main() -> int:
    env: dict[str, str] = {}
    env.update(load_env(ROOT / ".env"))
    env.update(load_env(ROOT / "infra" / ".env"))
    env.update(load_env(API / ".env"))

    account = (env.get("R2_ACCOUNT_ID") or "").strip()
    access = (env.get("R2_ACCESS_KEY_ID") or "").strip()
    secret = (env.get("R2_SECRET_ACCESS_KEY") or "").strip()
    bucket = (env.get("R2_BUCKET") or "dubby").strip()
    endpoint = (env.get("R2_ENDPOINT_URL") or "").strip()
    if not endpoint and account:
        endpoint = f"https://{account}.r2.cloudflarestorage.com"

    print("has_account=", bool(account))
    print("has_access=", bool(access))
    print("has_secret=", bool(secret))
    print("bucket=", bucket)
    print("endpoint_set=", bool(endpoint))
    if not (account and access and secret and endpoint):
        print("R2 credentials missing", file=sys.stderr)
        return 1

    import boto3
    from botocore.config import Config as BotoConfig

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        region_name="auto",
        config=BotoConfig(signature_version="s3v4"),
    )
    client.put_bucket_cors(
        Bucket=bucket,
        CORSConfiguration={"CORSRules": CORS_RULES},
    )
    current = client.get_bucket_cors(Bucket=bucket)
    origins = []
    for rule in current.get("CORSRules") or []:
        origins.extend(rule.get("AllowedOrigins") or [])
    print("cors_applied=True")
    print("allowed_origins=", ",".join(origins))
    print("rules=", json.dumps(current.get("CORSRules") or [], ensure_ascii=True)[:500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
