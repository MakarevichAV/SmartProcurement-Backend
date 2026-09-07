"""Vendored LORM reference implementation (unmodified). See UPSTREAM.md for the pin."""

from pathlib import Path

VENDOR_DIR = Path(__file__).resolve().parent
POLICY_SCHEMA_PATH = VENDOR_DIR / "lorm-policy.schema.json"
