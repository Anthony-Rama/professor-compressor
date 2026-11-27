"""Shared colour tokens and base styles inlined into every rendered page."""

import re

from ...paths import TEMPLATES

_LIGHT_RULE = re.compile(r'    :root\[data-theme="light"\] \{\n(.*?)\n    \}\n', re.S)


def _theme_css() -> str:
    """Tokens with the light palette applied by choice or by the device setting.

    theme.css writes the light palette once, for an explicit Light choice. The
    same declarations also apply when no theme is chosen and the device
    prefers light, so the System choice keeps following the device.
    """
    css = (TEMPLATES / "theme.css").read_text(encoding="utf-8").rstrip("\n")
    match = _LIGHT_RULE.search(css)
    if match is None:
        raise RuntimeError("theme.css is missing its light palette")
    declarations = "\n".join("  " + line for line in match.group(1).split("\n"))
    followed = (
        "    @media (prefers-color-scheme: light) {\n"
        "      :root:not([data-theme]) {\n"
        f"{declarations}\n"
        "      }\n"
        "    }\n"
    )
    return css[: match.end()] + followed + css[match.end() :]


THEME_CSS = _theme_css()

# Runs first in <head> so a saved Light or Dark choice applies before the first
# paint. The choice stays in this browser; storage errors fall back to System.
THEME_BOOT_SCRIPT = (
    '<script>try{var t=localStorage.getItem("pc-theme");'
    'if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}'
    "catch(e){}</script>"
)

_THEME_OPTIONS = (("system", "System"), ("light", "Light"), ("dark", "Dark"))

# Header control on the public website: a menu button listing the choices.
THEME_MENU = (
    '<div class="theme-menu" hidden>'
    '<button type="button" class="theme-menu-button" data-choice="system"'
    ' aria-haspopup="menu" aria-expanded="false" aria-controls="theme-menu-list"'
    ' aria-label="Theme: System" title="Theme: System">'
    '<span class="icon" aria-hidden="true"></span></button>'
    '<div class="theme-menu-list" id="theme-menu-list" role="menu"'
    ' aria-label="Theme" hidden>'
    + "".join(
        f'<button type="button" role="menuitemradio" tabindex="-1"'
        f' data-choice="{value}"'
        f' aria-checked="{str(value == "system").lower()}">'
        f'<span class="icon icon-theme-{value}" aria-hidden="true"></span>{name}'
        '<span class="icon icon-check menu-check" aria-hidden="true"></span></button>'
        for value, name in _THEME_OPTIONS
    )
    + "</div></div>"
)

# Loaded independently of the compressor: a failure here is caught and ignored.
THEME_TOGGLE_LOADER = (
    '<script type="module">import("/brand/theme-toggle.js").catch(() => {});</script>'
)

BRAND_MARK = (
    '<span class="brandmark"><img src="/brand/professor-compressor.png" alt=""'
    ' width="32" height="32">Professor Compressor</span>'
)

# Simple Icons "Discord" path; decorative, so it is hidden from assistive technology.
DISCORD_ICON = (
    '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"'
    ' aria-hidden="true" focusable="false"><path d="M20.317 4.37a19.79 19.79 0 0 0'
    "-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.865-.608 1.25a18.27 18.27 0 0"
    " 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.74 19.74 0 0 0"
    " 3.677 4.37a.07.07 0 0 0-.032.028C.533 9.046-.319 13.58.099 18.058a.082.082 0 0"
    " 0 .031.056 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028c.462-.63.873-1.295"
    " 1.226-1.994a.076.076 0 0 0-.041-.106 13.11 13.11 0 0 1-1.872-.892.077.077 0 0"
    " 1-.008-.128c.126-.094.252-.192.372-.291a.074.074 0 0 1 .078-.011c3.928 1.793"
    " 8.18 1.793 12.061 0a.074.074 0 0 1 .079.01c.12.099.246.198.373.292a.077.077 0 0"
    " 1-.007.128 12.3 12.3 0 0 1-1.873.891.077.077 0 0 0-.041.107c.36.698.772 1.363"
    " 1.225 1.993a.076.076 0 0 0 .084.029 19.84 19.84 0 0 0 6.002-3.03.077.077 0 0 0"
    " .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03ZM8.02"
    " 15.331c-1.183 0-2.157-1.086-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0"
    " 2.176 1.095 2.157 2.419 0 1.333-.956 2.419-2.157 2.419Zm7.975 0c-1.183"
    " 0-2.157-1.086-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.21 0 2.176 1.095"
    ' 2.157 2.419 0 1.333-.946 2.419-2.157 2.419Z"/></svg>'
)

MAIL_ICON = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'
    ' aria-hidden="true" focusable="false">'
    '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m4 7 8 6 8-6"/></svg>'
)


def with_shared_markup(fragment: str) -> str:
    """Fill the brand and icon placeholders used by page templates."""
    return (
        fragment.replace("__BRAND__", BRAND_MARK)
        .replace("__DISCORD_ICON__", DISCORD_ICON)
        .replace("__MAIL_ICON__", MAIL_ICON)
        .replace("__THEME_MENU__", THEME_MENU)
    )
