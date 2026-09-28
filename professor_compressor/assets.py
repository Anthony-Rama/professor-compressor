"""Versioned URLs for the unmodified, pinned browser packages."""

import hashlib
from pathlib import Path

NODE_MODULES = Path(__file__).resolve().parent.parent / "node_modules"
ASSET_PACKAGES = {
    "ffmpeg-esm": "@ffmpeg/ffmpeg",
    "util-esm": "@ffmpeg/util",
    "core-esm": "@ffmpeg/core",
    "core-mt-esm": "@ffmpeg/core-mt",
}


def asset_version() -> str:
    digest = hashlib.sha256()
    for package in ASSET_PACKAGES.values():
        manifest = NODE_MODULES / package / "package.json"
        digest.update(manifest.read_bytes() if manifest.exists() else package.encode())
    return digest.hexdigest()[:16]


ASSET_VERSION = asset_version()
ASSET_PREFIX = f"/assets/{ASSET_VERSION}"
