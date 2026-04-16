#!/usr/bin/env python3
"""
generate_icons.py — Generate and upload 8×8 team icons to AWTRIX3.

Design: Jersey vertical-stripe pattern (8px wide × 8px tall)
  cols 0-2 : primary color
  cols 3-4 : secondary color (chest stripe)
  cols 5-7 : accent color if 3 colors, else primary

Upload: POST multipart to http://{awtrix_host}/edit (AWTRIX3 LittleFS editor)
        Icons are stored as /ICONS/{slug}.jpg and referenced by name in payloads.

Set awtrix_host in add-on options to enable auto-upload.
Icons are always saved to /data/icons/ regardless.
"""

import io
import json
import re
import sys
import unicodedata
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("[generate_icons] Pillow not installed – skipping icon generation")
    sys.exit(0)

# ---------------------------------------------------------------------------
# Team color table  (primary, secondary, accent_or_None)
#
# Keys: lowercase slug, Swedish chars transliterated, spaces→underscore.
# Add multiple aliases per team (various name forms from swehockey.se).
# ---------------------------------------------------------------------------
TEAM_COLORS: dict[str, tuple[str, str, str | None]] = {
    # ============================================================
    # SHL
    # ============================================================
    "brynas_if": ("#006633", "#FFD700", None),
    "brynas": ("#006633", "#FFD700", None),
    "djurgardens_if": ("#00297A", "#CE2029", "#FFD700"),
    "djurgardens": ("#00297A", "#CE2029", "#FFD700"),
    "farjestad_bk": ("#00542A", "#FFD700", None),
    "farjestad": ("#00542A", "#FFD700", None),
    "frolunda_hc": ("#6B1D2A", "#FFD700", None),
    "frolunda": ("#6B1D2A", "#FFD700", None),
    "hc_frolunda": ("#6B1D2A", "#FFD700", None),
    "hv71": ("#FFD700", "#FFFFFF", "#003F7F"),
    "linkoping_hc": ("#003F6D", "#FFFFFF", None),
    "linkoping": ("#003F6D", "#FFFFFF", None),
    "lhc": ("#003F6D", "#FFFFFF", None),
    "lulea_hf": ("#CC0000", "#FFFFFF", "#004080"),
    "lulea": ("#CC0000", "#FFFFFF", "#004080"),
    "malmo_redhawks": ("#CC0000", "#000000", "#FFFFFF"),
    "malmo": ("#CC0000", "#000000", "#FFFFFF"),
    "modo_hockey": ("#CC0000", "#FFFFFF", "#000000"),
    "modo": ("#CC0000", "#FFFFFF", "#000000"),
    "rogle_bk": ("#ffffff", "#067b35", None),
    "rogle": ("#ffffff", "#067b35", None),
    "skelleftea_aik": ("#FFD700", "#000000", None),
    "skelleftea": ("#FFD700", "#000000", None),
    "saik": ("#FFD700", "#000000", None),
    "timra_ik": ("#CC0000", "#FFFFFF", "#000000"),
    "timra": ("#CC0000", "#FFFFFF", "#000000"),
    "orebro_hk": ("#001B50", "#FFFFFF", "#FFD700"),
    "orebro": ("#001B50", "#FFFFFF", "#FFD700"),
    "vaxjo_lakers": ("#052f5d", "#eb7229", None),
    "vaxjo": ("#052f5d", "#eb7229", None),
    # ============================================================
    # HockeyAllsvenskan
    # ============================================================
    "aik": ("#000000", "#FFD700", None),
    "almtuna_is": ("#CC0000", "#FFFFFF", None),
    "almtuna": ("#CC0000", "#FFFFFF", None),
    "bik_karlskoga": ("#003F7F", "#FFD700", None),
    "karlskoga": ("#003F7F", "#FFD700", None),
    "if_bjorkloven": ("#0b5640", "#fdd003", None),
    "bjorkloven": ("#0b5640", "#fdd003", None),
    "ik_oskarshamn": ("#003F7F", "#FFFFFF", "#CC0000"),
    "oskarshamn": ("#003F7F", "#FFFFFF", "#CC0000"),
    "karlskrona_hk": ("#003F7F", "#FFFFFF", None),
    "karlskrona": ("#003F7F", "#FFFFFF", None),
    "kristianstad_ik": ("#CC0000", "#FFFFFF", "#000000"),
    "kristianstad": ("#CC0000", "#FFFFFF", "#000000"),
    "mora_ik": ("#003F7F", "#FFD700", None),
    "mora": ("#003F7F", "#FFD700", None),
    "nybro_vikings_if": ("#006633", "#CC0000", "#FFFFFF"),
    "nybro": ("#006633", "#CC0000", "#FFFFFF"),
    "tingsryd_aif": ("#CC0000", "#FFFFFF", None),
    "tingsryd": ("#CC0000", "#FFFFFF", None),
    "vik_vasteras_hk": ("#00388A", "#FFFFFF", "#FFD700"),
    "vasteras": ("#00388A", "#FFFFFF", "#FFD700"),
    "vastervik_ik": ("#007755", "#FFFFFF", None),
    "vastervik": ("#007755", "#FFFFFF", None),
    "sodertalje_sk": ("#003F7F", "#FFFFFF", "#CC0000"),
    "sodertalje": ("#003F7F", "#FFFFFF", "#CC0000"),
    "huddinge_ik": ("#CC0000", "#FFFFFF", None),
    "huddinge": ("#CC0000", "#FFFFFF", None),
    # ============================================================
    # CHL / international (common opponents)
    # ============================================================
    "frolunda_gothenburg": ("#6B1D2A", "#FFD700", None),
    "tappara": ("#CC0000", "#FFFFFF", None),
    "karpat": ("#CC0000", "#000000", None),
    "tps": ("#003F7F", "#FFFFFF", None),
    "ilves": ("#FFD700", "#000000", None),
    "pelicans": ("#CC0000", "#FFFFFF", None),
    "jokerit": ("#CC0000", "#FFFFFF", None),
    "hifk": ("#CC0000", "#FFFFFF", None),
    "lukko": ("#003F7F", "#FFFFFF", None),
    "hpk": ("#CC0000", "#000000", None),
    "jyvaskyla": ("#003F7F", "#FFFFFF", None),
    "espoo_blues": ("#003F7F", "#FFFFFF", None),
    "khl": ("#CC0000", "#003F7F", None),
    # ============================================================
    # Fallback generic colors per first letter (used if no match)
    # ============================================================
}

# ---------------------------------------------------------------------------
# Helper: normalize team name to lookup key
# ---------------------------------------------------------------------------
_TRANSLIT = str.maketrans(
    "åäöÅÄÖéüÜ",
    "aaoAAOeuu",
)


def team_slug(name: str) -> str:
    """Normalize a team name to a slug for TEAM_COLORS lookup."""
    s = name.translate(_TRANSLIT).lower()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s


def lookup_colors(team_name: str) -> tuple[str, str, str | None] | None:
    """Return (primary, secondary, accent) for a team name, or None if unknown."""
    slug = team_slug(team_name)
    if slug in TEAM_COLORS:
        return TEAM_COLORS[slug]
    # Try prefix match (e.g. "Björklövens IF" → "bjorkloven")
    for key, colors in TEAM_COLORS.items():
        if slug.startswith(key) or key.startswith(slug):
            return colors
    return None


# ---------------------------------------------------------------------------
# Icon generation
# ---------------------------------------------------------------------------


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def make_icon_jpeg(primary: str, secondary: str, accent: str | None = None) -> bytes:
    """
    Generate an 8×8 jersey icon as GIF bytes (lossless, pixel-perfect).

    Layout (columns):
      0-2  : primary
      3-4  : secondary
      5-7  : accent (if given) else primary
    """
    p = _hex_to_rgb(primary)
    s = _hex_to_rgb(secondary)
    a = _hex_to_rgb(accent) if accent else None

    img = Image.new("P", (8, 8))
    # Build a minimal palette with exactly our colors
    palette_colors = [p, s, a if a else p]
    flat = [c for rgb in palette_colors for c in rgb]
    flat += [0] * (768 - len(flat))
    img.putpalette(flat)
    px = img.load()
    for y in range(8):
        for x in range(8):
            px[x, y] = 0  # primary
        px[3, y] = 1  # secondary
        px[4, y] = 1  # secondary
        if a:
            px[5, y] = 2  # accent
            px[6, y] = 2
            px[7, y] = 2

    buf = io.BytesIO()
    img.save(buf, format="GIF")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# AWTRIX upload
# ---------------------------------------------------------------------------


def upload_icon(host: str, icon_name: str, jpeg_bytes: bytes) -> bool:
    """
    Upload icon to AWTRIX3 via its LittleFS web editor (/edit endpoint).
    The icon will be stored at /ICONS/{icon_name}.gif and referenced by
    icon_name in custom app payloads.

    Returns True on success.
    """
    import urllib.request

    boundary = "----HockeyLiveBoundary7MA4YWxkTrZu0gW"
    filename = f"{icon_name}.gif"
    # AWTRIX3 stores icons in /ICONS/ on LittleFS
    remote_path = f"/ICONS/{filename}"

    body = (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="data"; filename="{remote_path}"\r\n'
            f"Content-Type: image/gif\r\n\r\n"
        ).encode()
        + jpeg_bytes
        + f"\r\n--{boundary}--\r\n".encode()
    )

    req = urllib.request.Request(
        f"http://{host}/edit",
        data=body,
        method="POST",
    )
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Content-Length", str(len(body)))

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok = resp.status < 300
            if not ok:
                body_resp = resp.read().decode("utf-8", errors="replace")[:200]
                print(
                    f"[generate_icons] Upload {icon_name}: HTTP {resp.status} – {body_resp}"
                )
            return ok
    except Exception as exc:
        print(f"[generate_icons] Upload {icon_name} failed: {exc}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    options_file = Path("/data/options.json")
    if not options_file.exists():
        print("[generate_icons] /data/options.json not found – skipping")
        return

    with open(options_file) as f:
        opts = json.load(f)

    awtrix_host = (opts.get("awtrix_host") or "").strip()

    out_dir = Path("/data/icons")
    out_dir.mkdir(parents=True, exist_ok=True)

    uploaded = skipped = saved = 0
    # Deduplicate: generate one icon per unique color combination
    seen_slugs: set[str] = set()
    unique_teams: dict[str, tuple] = {}
    for slug, colors in TEAM_COLORS.items():
        if colors not in unique_teams.values():
            unique_teams[slug] = colors

    for slug, (primary, secondary, accent) in unique_teams.items():
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        try:
            jpeg = make_icon_jpeg(primary, secondary, accent)
            (out_dir / f"{slug}.gif").write_bytes(jpeg)
            saved += 1

            if awtrix_host:
                ok = upload_icon(awtrix_host, slug, jpeg)
                if ok:
                    uploaded += 1
                    print(f"[generate_icons] ✓ {slug}")
                else:
                    skipped += 1
        except Exception as exc:
            print(f"[generate_icons] Error for {slug}: {exc}")
            skipped += 1

    if awtrix_host:
        print(
            f"[generate_icons] {uploaded} uploaded, {skipped} failed"
            f" – icons also saved to {out_dir}"
        )
    else:
        print(
            f"[generate_icons] {saved} icons saved to {out_dir}"
            f" (set awtrix_host in options to auto-upload)"
        )


if __name__ == "__main__":
    main()
