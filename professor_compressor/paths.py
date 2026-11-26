"""Filesystem locations of packaged templates, static files, and browser assets."""

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = PACKAGE_ROOT.parent
TEMPLATES = PACKAGE_ROOT / "templates"
STATIC = PACKAGE_ROOT / "static"
# Installed by npm next to the package, both locally and at /app in the image.
NODE_MODULES = REPOSITORY_ROOT / "node_modules"
