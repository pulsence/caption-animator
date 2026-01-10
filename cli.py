#!/usr/bin/env python3
"""
render_overlay.py — Render subtitle overlays (SRT/ASS) into a transparent video for DaVinci Resolve.

Implements:
- Accepts .srt and .ass inputs (ASS is first-class; preserved by default)
- Preset-driven styling (YAML/JSON) for SRT → ASS conversion (and optional "reskin" for ASS)
- Optional line-level animations (fade / slide_up / scale_settle / blur_settle)
- Computes a "tight" fixed overlay resolution: maximum required text box across the full subtitle file
  (Overlay video frame size is constant; we size it to the maximum bounding box needed.)
- Renders a transparent overlay video (MOV ProRes 4444 with alpha) for Resolve positioning

Dependencies:
  pip install pysubs2 pillow pyyaml
FFmpeg:
  ffmpeg must be available on PATH.

Notes / constraints:
- Video overlays must be a single fixed resolution; we compute the maximum required size across all events.
- True rounded boxes are not supported by ASS; "box" here is approximated via outline/shadow/readability settings.
- Text measurement is done with Pillow. Rendering is done by FFmpeg/libass. They usually match closely.
  To avoid edge clipping in rare cases, use --safety-scale (e.g. 1.10–1.20).

Example usage:
  python render_overlay.py subtitles.srt --preset presets.yaml:modern_box --out overlay.mov
  python render_overlay.py subtitles.ass --out overlay.mov
  python render_overlay.py subtitles.ass --preset presets.yaml:clean_outline --reskin --out overlay.mov

Preset addressing:
  --preset can be:
    - A file path containing a single preset dict
    - "path/to/presets.yaml:preset_name" for multi-preset files
    - A built-in preset name: "modern_box" or "clean_outline" (defaults provided)

Outputs:
  - overlay.mov (ProRes 4444)
  - optionally overlay.ass if --keep-ass

Author: (you)
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List

import pysubs2
from PIL import ImageFont

try:
    import yaml  # type: ignore
except Exception:
    yaml = None


# -----------------------------
# Presets (built-in defaults)
# -----------------------------

BUILTIN_PRESETS: Dict[str, Dict[str, Any]] = {
    # A conservative, readable default with heavier outline.
    "clean_outline": {
        "font_file": "",  # strongly recommended to set; if empty, will attempt system fallback
        "font_name": "Arial",
        "font_size": 64,
        "bold": False,
        "italic": False,
        "primary_color": "#FFFFFF",
        "outline_color": "#000000",
        "shadow_color": "#000000",
        "outline_px": 5,
        "shadow_px": 2,
        "blur_px": 0,
        "line_spacing": 8,  # px
        "max_width_px": 1200,  # wrap width; keeps overlay reasonable
        "padding": [40, 60, 50, 60],  # [top, right, bottom, left]
        "alignment": 2,  # ASS: 2 = bottom-center
        "margin_l": 0,
        "margin_r": 0,
        "margin_v": 0,  # since we will position within tight canvas; padding handles space
        "wrap_style": 2,  # 2 = smart wrapping
        "animation": {
            "type": "fade",
            "in_ms": 120,
            "out_ms": 120,
        },
    },
    # “Modern box-like” readability without true rounded box.
    # We approximate “boxed captions” by using slightly heavier outline + shadow and extra padding,
    # and recommend using Resolve's Fusion/Text+ for true rounded rectangles if needed.
    "modern_box": {
        "font_file": "",
        "font_name": "Arial",
        "font_size": 62,
        "bold": True,
        "italic": False,
        "primary_color": "#FFFFFF",
        "outline_color": "#000000",
        "shadow_color": "#000000",
        "outline_px": 6,
        "shadow_px": 3,
        "blur_px": 0,
        "line_spacing": 10,
        "max_width_px": 1100,
        "padding": [44, 70, 56, 70],
        "alignment": 2,
        "margin_l": 0,
        "margin_r": 0,
        "margin_v": 0,
        "wrap_style": 2,
        "animation": {
            "type": "slide_up",
            "in_ms": 140,
            "out_ms": 120,
            "move_px": 26,
        },
    },
}


# -----------------------------
# Utilities
# -----------------------------

class Progress:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._t0 = time.time()

    def step(self, msg: str) -> None:
        if not self.enabled:
            return
        elapsed = time.time() - self._t0
        eprint(f"[{elapsed:6.1f}s] {msg}")


def compute_subs_end_ms(subs: pysubs2.SSAFile) -> int:
    """
    Returns the maximum end time (ms) across all subtitle events.
    """
    end_ms = 0
    for ev in subs.events:
        if isinstance(ev, pysubs2.SSAEvent):
            end_ms = max(end_ms, int(ev.end))
    return end_ms


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


def die(msg: str, code: int = 2) -> None:
    eprint(f"ERROR: {msg}")
    raise SystemExit(code)


def which_or_die(cmd: str) -> str:
    path = shutil.which(cmd)
    if not path:
        die(f"'{cmd}' not found on PATH. Install FFmpeg and ensure it is available.")
    return path


def load_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def load_preset(preset_ref: str) -> Dict[str, Any]:
    """
    preset_ref:
      - built-in name: "modern_box"
      - "path/to/preset.yaml" (single dict)
      - "path/to/presets.yaml:name" (multi-preset dict)
      - "path/to/presets.json:name"
    """
    if preset_ref in BUILTIN_PRESETS:
        return dict(BUILTIN_PRESETS[preset_ref])

    if ":" in preset_ref and Path(preset_ref.split(":", 1)[0]).exists():
        file_part, name_part = preset_ref.split(":", 1)
        p = Path(file_part)
        data = load_preset_file(p)
        if not isinstance(data, dict):
            die(f"Preset file '{p}' must contain a dict (mapping).")
        if name_part not in data:
            die(f"Preset '{name_part}' not found in '{p}'. Available: {', '.join(sorted(data.keys()))}")
        preset = data[name_part]
        if not isinstance(preset, dict):
            die(f"Preset '{name_part}' in '{p}' must be a dict.")
        return dict(preset)

    p = Path(preset_ref)
    if p.exists():
        data = load_preset_file(p)
        if isinstance(data, dict) and all(isinstance(k, str) for k in data.keys()):
            # Could be a single preset dict or a dict of presets.
            # If it "looks like" a single preset (contains font_size or padding), treat as single.
            if "font_size" in data or "padding" in data or "max_width_px" in data:
                return dict(data)
            # Otherwise ambiguous multi-preset: require name.
            die(f"Preset file '{p}' appears to contain multiple presets. Use '{p}:preset_name'.")
        die(f"Preset file '{p}' must be a dict.")
    die(f"Preset '{preset_ref}' not found (not a built-in name and file does not exist).")


def load_preset_file(path: Path) -> Any:
    ext = path.suffix.lower()
    text = load_text_file(path)
    if ext in (".yaml", ".yml"):
        if yaml is None:
            die("pyyaml is not installed. Install with: pip install pyyaml")
        return yaml.safe_load(text)
    if ext == ".json":
        return json.loads(text)
    die(f"Unsupported preset file extension '{ext}'. Use .yaml/.yml or .json.")


def parse_hex_color(color: str) -> Tuple[int, int, int]:
    """
    "#RRGGBB" -> (r,g,b)
    """
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", color.strip())
    if not m:
        die(f"Invalid color '{color}'. Use #RRGGBB.")
    s = m.group(1)
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def ms_to_ass_time(ms: int) -> str:
    """
    pysubs2 handles timings, but sometimes helpful.
    """
    total = ms / 1000.0
    h = int(total // 3600)
    m = int((total % 3600) // 60)
    s = total % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def clamp_int(x: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, x))


# -----------------------------
# Animation tag injection
# -----------------------------

def build_animation_override(anim: Dict[str, Any]) -> str:
    r"""
    Returns an ASS override tag string (without surrounding braces) for line-level animation.

    Supported:
      - none
      - fade: \fad(in,out)
      - slide_up: \move(x, y+dy, x, y, 0, in_ms) + optional fade-out
      - scale_settle: start at 110% -> 100% over in_ms (via \t)
      - blur_settle: start blurred -> sharp over in_ms (via \t)
    """
    if not anim:
        return ""

    atype = (anim.get("type") or "none").strip().lower()
    if atype in ("none", ""):
        return ""

    in_ms = int(anim.get("in_ms", 120))
    out_ms = int(anim.get("out_ms", 120))

    # Note: \move requires absolute coordinates; we instead do a relative trick by
    # using \pos and \move with the same X but different Y, assuming we also set \pos.
    # We'll implement slide by appending \move(x,y+dy,x,y,0,in_ms) and later also set \pos.
    # In practice, we compute final x,y at render time only indirectly. For tight overlays we
    # anchor subtitles with alignment at canvas center/bottom; we can place text with \pos.

    if atype == "fade":
        return fr"\fad({clamp_int(in_ms,0,2000)},{clamp_int(out_ms,0,2000)})"

    if atype == "scale_settle":
        start = int(anim.get("start_scale", 110))
        end = int(anim.get("end_scale", 100))
        accel = float(anim.get("accel", 1.0))
        # Start scaled; transform to end over in_ms
        return fr"\fscx{start}\fscy{start}\t(0,{clamp_int(in_ms,0,4000)},{accel},\fscx{end}\fscy{end})\fad({clamp_int(in_ms,0,2000)},{clamp_int(out_ms,0,2000)})"

    if atype == "blur_settle":
        start_blur = int(anim.get("start_blur", 4))
        end_blur = int(anim.get("end_blur", 0))
        accel = float(anim.get("accel", 1.0))
        return fr"\blur{start_blur}\t(0,{clamp_int(in_ms,0,4000)},{accel},\blur{end_blur})\fad({clamp_int(in_ms,0,2000)},{clamp_int(out_ms,0,2000)})"

    if atype == "slide_up":
        # We'll need to set \pos(x,y) and \move(...) using the computed anchor.
        # We'll emit a placeholder that later gets formatted with actual x,y.
        move_px = int(anim.get("move_px", 24))
        # tokens replaced later: {X} {Y} {DY}
        return fr"\fad({clamp_int(in_ms,0,2000)},{clamp_int(out_ms,0,2000)})\move({{X}},{{Y_PLUS_DY}},{{X}},{{Y}},0,{clamp_int(in_ms,0,4000)})"

    die(f"Unsupported animation type '{atype}'.")


def apply_animation_to_event_text(text: str, anim_override: str) -> str:
    """
    Ensure override tags are applied at beginning of the line.
    """
    if not anim_override:
        return text
    # If text begins with an override block {..}, prepend inside it.
    if text.startswith("{") and "}" in text:
        end = text.find("}")
        head = text[1:end]
        rest = text[end+1:]
        # Avoid duplicating if already has fad/move etc (best-effort)
        return "{" + anim_override + head + "}" + rest
    return "{" + anim_override + "}" + text


# -----------------------------
# Text wrapping + measurement
# -----------------------------

def normalize_whitespace(s: str) -> str:
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    # Keep explicit newlines; collapse multiple spaces
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def wrap_text_to_width(text: str, font: ImageFont.FreeTypeFont, max_width_px: int) -> str:
    """
    Wrap text (keeping existing line breaks) so that each line <= max_width_px.
    Simple greedy word-wrap. For subtitles, this is typically sufficient.
    """
    text = normalize_whitespace(text)
    if max_width_px <= 0:
        return text

    lines_in = text.split("\n")
    lines_out: List[str] = []

    for raw_line in lines_in:
        raw_line = raw_line.strip()
        if not raw_line:
            lines_out.append("")
            continue

        words = raw_line.split(" ")
        current: List[str] = []
        for w in words:
            if not current:
                current = [w]
                continue
            candidate = " ".join(current + [w])
            if font.getlength(candidate) <= max_width_px:
                current.append(w)
            else:
                lines_out.append(" ".join(current))
                current = [w]
        if current:
            lines_out.append(" ".join(current))

    return "\n".join(lines_out)


def measure_multiline(text: str, font: ImageFont.FreeTypeFont, line_spacing_px: int) -> Tuple[int, int, int]:
    """
    Returns (max_line_width_px, total_height_px, line_count)
    """
    lines = text.split("\n") if text else [""]
    widths = [int(math.ceil(font.getlength(line))) for line in lines]
    max_w = max(widths) if widths else 0

    # Pillow doesn't include line spacing in font metrics directly for multi-line; approximate:
    ascent, descent = font.getmetrics()
    line_h = ascent + descent
    total_h = len(lines) * line_h + max(0, len(lines) - 1) * int(line_spacing_px)

    return max_w, total_h, len(lines)


def strip_ass_tags(text: str) -> str:
    """
    Remove ASS override tags like {\\...} to measure visible text.
    """
    # Remove override blocks
    text = re.sub(r"\{[^}]*\}", "", text)
    return text


# -----------------------------
# ASS creation / styling
# -----------------------------
def make_pysubs2_color(rgb: Tuple[int, int, int], alpha: int = 0):
    """
    Create a pysubs2 Color across versions.
    alpha: 0=opaque, 255=fully transparent (ASS alpha convention used by pysubs2 Color).
    """
    r, g, b = rgb
    # Most pysubs2 versions support Color(r,g,b,a)
    try:
        return pysubs2.Color(r, g, b, alpha)
    except TypeError:
        # Fallback: some older variants may accept only (r,g,b)
        return pysubs2.Color(r, g, b)


def build_ass_style_from_preset(preset: Dict[str, Any], style_name: str = "Default") -> pysubs2.SSAStyle:
    """
    Map preset to pysubs2 SSAStyle.
    """
    fontname = str(preset.get("font_name", "Arial"))
    fontsize = int(preset.get("font_size", 64))
    bold = bool(preset.get("bold", False))
    italic = bool(preset.get("italic", False))

    primary_rgb = parse_hex_color(str(preset.get("primary_color", "#FFFFFF")))
    outline_rgb = parse_hex_color(str(preset.get("outline_color", "#000000")))
    shadow_rgb = parse_hex_color(str(preset.get("shadow_color", "#000000")))

    outline = float(preset.get("outline_px", 4))
    shadow = float(preset.get("shadow_px", 2))
    blur = float(preset.get("blur_px", 0))

    alignment = int(preset.get("alignment", 2))
    margin_l = int(preset.get("margin_l", 0))
    margin_r = int(preset.get("margin_r", 0))
    margin_v = int(preset.get("margin_v", 0))

    st = pysubs2.SSAStyle()
    st.fontname = fontname
    st.fontsize = fontsize
    st.bold = -1 if bold else 0
    st.italic = -1 if italic else 0

    st.primarycolor = make_pysubs2_color(primary_rgb, alpha=0)
    st.outlinecolor = make_pysubs2_color(outline_rgb, alpha=0)
    st.backcolor = make_pysubs2_color(shadow_rgb, alpha=0)

    st.outline = outline
    st.shadow = shadow
    st.spacing = 0
    st.alignment = alignment
    st.marginl = margin_l
    st.marginr = margin_r
    st.marginv = margin_v
    # Blur isn't in SSAStyle directly; we will add \blur override at event-level if requested.
    # However, baseline blur can be expressed with \blur in each event if preset blur_px > 0.

    return st


def ensure_font_available(preset: Dict[str, Any]) -> Tuple[Optional[Path], str]:
    """
    Returns (font_file_path_or_None, font_name).
    If font_file is provided and exists, use it (recommended).
    Otherwise rely on system font_name.
    """
    font_file = str(preset.get("font_file", "")).strip()
    if font_file:
        p = Path(font_file)
        if not p.exists():
            die(f"font_file '{p}' does not exist.")
        return p, str(preset.get("font_name", "Arial"))
    return None, str(preset.get("font_name", "Arial"))


def pick_pillow_font(preset: Dict[str, Any]) -> ImageFont.FreeTypeFont:
    """
    Prefer font_file for deterministic measurement. If absent, attempt system fallback by name.
    Pillow cannot always load by family name cross-platform; if no font_file is supplied,
    measurement may be less deterministic. For best results, always specify font_file.
    """
    font_file, font_name = ensure_font_available(preset)
    size = int(preset.get("font_size", 64))

    if font_file is not None:
        return ImageFont.truetype(str(font_file), size=size)

    # Best-effort fallback:
    # On Windows, 'arial.ttf' is common; on macOS, 'Arial.ttf' often exists; Linux varies.
    candidates = [
        "arial.ttf",
        "Arial.ttf",
        "DejaVuSans.ttf",
        "LiberationSans-Regular.ttf",
    ]
    for c in candidates:
        try:
            return ImageFont.truetype(c, size=size)
        except Exception:
            continue

    die(
        "No usable font found for Pillow measurement. Provide --preset with 'font_file' set "
        "to a TTF/OTF path for deterministic results."
    )


def convert_srt_to_ass(
    subs: pysubs2.SSAFile,
    preset: Dict[str, Any],
    apply_animation: bool,
) -> pysubs2.SSAFile:
    """
    Takes an SSAFile loaded from SRT, produces an ASS SSAFile with Default style.
    Also wraps lines per preset max_width_px and injects animations.
    """
    out = pysubs2.SSAFile()
    out.info = dict(subs.info)
    out.styles = dict(subs.styles)
    out.events = []

    style = build_ass_style_from_preset(preset, style_name="Default")
    out.styles["Default"] = style

    wrap_style = int(preset.get("wrap_style", 2))
    out.info["WrapStyle"] = str(wrap_style)
    out.info["ScaledBorderAndShadow"] = "yes"
    out.info["ScriptType"] = "v4.00+"

    font = pick_pillow_font(preset)
    max_width_px = int(preset.get("max_width_px", 0))
    line_spacing_px = int(preset.get("line_spacing", 8))

    anim_override = ""
    if apply_animation:
        anim_override = build_animation_override(dict(preset.get("animation") or {}))

    base_blur = float(preset.get("blur_px", 0))
    base_blur_override = fr"\blur{int(base_blur)}" if base_blur and base_blur > 0 else ""

    for ev in subs.events:
        if not isinstance(ev, pysubs2.SSAEvent):
            continue
        text = normalize_whitespace(ev.text)
        text = wrap_text_to_width(text, font, max_width_px=max_width_px)
        # Apply base blur and animations
        # Ensure base blur is always applied first for consistent look
        combined_override = ""
        if base_blur_override:
            combined_override += base_blur_override
        if anim_override:
            combined_override += anim_override
        if combined_override:
            text = apply_animation_to_event_text(text, combined_override)

        new_ev = pysubs2.SSAEvent(
            start=ev.start,
            end=ev.end,
            text=text,
            style="Default",
        )
        out.events.append(new_ev)

    return out


def reskin_ass(
    subs: pysubs2.SSAFile,
    preset: Dict[str, Any],
    strip_existing_overrides: bool,
    apply_animation: bool,
) -> pysubs2.SSAFile:
    """
    Apply preset style to ASS.
    - Updates/creates style "Default" and assigns it to all events.
    - Optionally strips override tags from events.
    - Optionally injects animation overrides (useful if the ASS doesn't already contain them).
    """
    out = subs

    out.styles["Default"] = build_ass_style_from_preset(preset, style_name="Default")
    wrap_style = int(preset.get("wrap_style", 2))
    out.info["WrapStyle"] = str(wrap_style)
    out.info["ScaledBorderAndShadow"] = "yes"

    font = pick_pillow_font(preset)
    max_width_px = int(preset.get("max_width_px", 0))
    anim_override = ""
    if apply_animation:
        anim_override = build_animation_override(dict(preset.get("animation") or {}))

    base_blur = float(preset.get("blur_px", 0))
    base_blur_override = fr"\blur{int(base_blur)}" if base_blur and base_blur > 0 else ""

    for ev in out.events:
        if not isinstance(ev, pysubs2.SSAEvent):
            continue
        ev.style = "Default"

        txt = ev.text
        if strip_existing_overrides:
            txt = strip_ass_tags(txt)

        txt = normalize_whitespace(txt)
        txt = wrap_text_to_width(txt, font, max_width_px=max_width_px)

        combined_override = ""
        if base_blur_override:
            combined_override += base_blur_override
        if anim_override:
            combined_override += anim_override
        if combined_override:
            txt = apply_animation_to_event_text(txt, combined_override)

        ev.text = txt

    return out


# -----------------------------
# Overlay sizing
# -----------------------------

@dataclass
class OverlaySize:
    width: int
    height: int


def compute_tight_overlay_size(
    subs: pysubs2.SSAFile,
    preset: Dict[str, Any],
    safety_scale: float,
) -> OverlaySize:
    """
    Compute maximum required overlay width/height across all events for a given preset.

    This uses Pillow measurement on visible text (ASS tags stripped) after wrapping rules,
    then adds padding + outline/shadow allowances.

    Returns a fixed overlay size (W,H) appropriate to render for the entire subtitle file.
    """
    font = pick_pillow_font(preset)
    max_width_px = int(preset.get("max_width_px", 0))
    line_spacing_px = int(preset.get("line_spacing", 8))
    padding = preset.get("padding", [40, 60, 50, 60])
    if not (isinstance(padding, list) and len(padding) == 4):
        die("Preset 'padding' must be a list of 4 integers: [top, right, bottom, left].")
    pad_t, pad_r, pad_b, pad_l = map(int, padding)

    outline_px = float(preset.get("outline_px", 4))
    shadow_px = float(preset.get("shadow_px", 2))

    max_w = 0
    max_h = 0

    for ev in subs.events:
        if not isinstance(ev, pysubs2.SSAEvent):
            continue
        txt = strip_ass_tags(ev.text)
        txt = normalize_whitespace(txt)
        txt = wrap_text_to_width(txt, font, max_width_px=max_width_px)

        w, h, _ = measure_multiline(txt, font, line_spacing_px=line_spacing_px)
        max_w = max(max_w, w)
        max_h = max(max_h, h)

    # Add allowances for outline + shadow:
    # Outline expands in all directions; shadow typically expands to bottom-right.
    # We'll conservatively add (outline*2 + shadow) to both dims.
    extra_w = int(math.ceil(outline_px * 2 + shadow_px * 2))
    extra_h = int(math.ceil(outline_px * 2 + shadow_px * 2))

    w_final = int(math.ceil((max_w + pad_l + pad_r + extra_w) * float(safety_scale)))
    h_final = int(math.ceil((max_h + pad_t + pad_b + extra_h) * float(safety_scale)))

    # Practical minimums:
    w_final = max(w_final, 64)
    h_final = max(h_final, 64)

    # libass sometimes prefers even dimensions in certain encodes; ProRes is fine, but keep tidy.
    if w_final % 2 == 1:
        w_final += 1
    if h_final % 2 == 1:
        h_final += 1

    return OverlaySize(width=w_final, height=h_final)


def compute_anchor_position(
    preset: Dict[str, Any],
    size: OverlaySize,
) -> Tuple[int, int]:
    """
    Compute a stable anchor (x,y) for placement inside the overlay canvas.
    We render centered horizontally and near bottom with padding.

    Because overlay is tight and will be positioned in Resolve, we use padding to keep
    comfortable breathing room.

    Returns (x, y) in pixels.
    """
    padding = preset.get("padding", [40, 60, 50, 60])
    pad_t, pad_r, pad_b, pad_l = map(int, padding)

    # Anchor point used for \pos when needed. We'll center horizontally.
    x = size.width // 2

    # For bottom-aligned text: anchor near bottom minus bottom padding.
    # ASS alignment will interpret anchor differently; but \pos with alignment=2 (bottom-center)
    # places the text bottom-center at x,y. So y should be height - pad_b.
    y = size.height - pad_b

    return x, y


def substitute_slide_placeholders(ass_path: Path, preset: Dict[str, Any], size: OverlaySize) -> None:
    """
    Replace slide-up animation placeholders {X} {Y} {Y_PLUS_DY} in the ASS file if used.

    This is a pragmatic approach: if the chosen animation contains the placeholders,
    we compute a consistent anchor position (x,y) and substitute actual coordinates.
    """
    anim = dict(preset.get("animation") or {})
    atype = (anim.get("type") or "none").strip().lower()
    if atype != "slide_up":
        return

    move_px = int(anim.get("move_px", 24))
    x, y = compute_anchor_position(preset, size)
    y_plus_dy = y + move_px

    txt = load_text_file(ass_path)
    txt = txt.replace("{X}", str(x)).replace("{Y}", str(y)).replace("{Y_PLUS_DY}", str(y_plus_dy))
    ass_path.write_text(txt, encoding="utf-8", errors="replace")


# -----------------------------
# FFmpeg rendering
# -----------------------------

def run_ffmpeg_render_overlay(
    ffmpeg: str,
    ass_path: Path,
    out_path: Path,
    size: OverlaySize,
    fps: str,
    loglevel: str,
    keep_temp: bool,
    duration_sec: float,
    show_progress: bool = True,
) -> None:
    """
    Render ASS subtitles to a transparent canvas and encode as ProRes 4444 (MOV) with alpha.
    """
    # Transparent canvas:
    # color=c=black@0.0 gives alpha 0. Some builds accept "0x00000000".
    # We'll use black@0.0 and explicitly set format to rgba early.
    # subtitles filter (libass) will render text with alpha.
    #
    # Output:
    # ProRes 4444: prores_ks profile 4; keep alpha with yuva444p10le
    # Also set pix_fmt explicitly.
    w, h = size.width, size.height

    # Ensure path is safely escaped for FFmpeg filter.
    # Using subtitles=filename=... requires escaping backslashes and colons on Windows.
    def ff_escape(p: Path) -> str:
        # Use forward slashes to avoid backslash escaping issues on Windows
        s = str(p.resolve()).replace("\\", "/")
        # FFmpeg filter uses ':' as a separator, must be escaped for drive letters
        s = s.replace(":", r"\:")
        # Escape single quotes because we wrap the whole thing in single quotes
        s = s.replace("'", r"\'")
        return s


    ass_esc = ff_escape(ass_path)

    vf = (
        f"format=rgba,"
        f"subtitles=filename='{ass_esc}':alpha=1:original_size={w}x{h},"
        f"format=yuva444p10le"
    )

    cmd = [
        ffmpeg, "-y",
        "-hide_banner",
        "-loglevel", loglevel,
        "-f", "lavfi",
        "-t", f"{duration_sec:.3f}",
        "-i", f"color=c=black@0.0:s={w}x{h}:r={fps}",
        "-vf", vf,
        "-c:v", "prores_ks",
        "-profile:v", "4",               # 4 = ProRes 4444
        "-pix_fmt", "yuva444p10le",
        "-r", fps,
        "-an",
        str(out_path),
    ]

    # If show_progress, use FFmpeg's machine-readable progress output on stderr.
    if show_progress:
        # Insert after ffmpeg binary:
        # ffmpeg -progress pipe:2 -nostats ...
        cmd.insert(1, "-progress")
        cmd.insert(2, "pipe:2")
        cmd.insert(3, "-nostats")

    eprint("FFmpeg command:")
    eprint("  " + " ".join(cmd))

    if not show_progress:
        p = subprocess.run(cmd)
        if p.returncode != 0:
            die("FFmpeg render failed. See output above for details.")
    else:
        proc = subprocess.Popen(
            cmd,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )

        last_print = time.time()
        frame = None
        out_time = None
        speed = None

        assert proc.stderr is not None
        for line in proc.stderr:
            line = line.strip()
            if not line or "=" not in line:
                continue

            k, v = line.split("=", 1)
            if k == "frame":
                frame = v
            elif k == "out_time":
                out_time = v
            elif k == "speed":
                speed = v
            elif k == "progress" and v == "end":
                break

            # Print at most twice per second
            now = time.time()
            if now - last_print >= 0.5 and (frame or out_time):
                msg = "FFmpeg"
                if frame:
                    msg += f" frame={frame}"
                if out_time:
                    msg += f" time={out_time}"
                if speed:
                    msg += f" speed={speed}"
                eprint(msg)
                last_print = now

        rc = proc.wait()
        if rc != 0:
            die("FFmpeg render failed. See output above for details.")

    if not out_path.exists() or out_path.stat().st_size < 1024:
        die("Output file not created or too small; render likely failed.")


# -----------------------------
# Main
# -----------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Render a transparent subtitle overlay video (ProRes 4444 alpha) for DaVinci Resolve."
    )
    ap.add_argument("input", help="Input subtitle file (.srt or .ass)")
    ap.add_argument(
        "--out",
        default=None,
        help="Output overlay video path (.mov recommended). Default: <inputfile>.mov",
    )
    ap.add_argument(
        "--preset",
        default="modern_box",
        help=(
            "Preset reference (built-in name like 'modern_box'/'clean_outline', "
            "or 'presets.yaml:preset_name', or a single preset file). "
            "Required for SRT; optional for ASS."
        ),
    )
    ap.add_argument(
        "--no-preset-for-ass",
        action="store_true",
        help="If input is .ass, ignore --preset unless --reskin is provided.",
    )
    ap.add_argument(
        "--reskin",
        action="store_true",
        help="For .ass inputs: apply the preset style to Default and assign all events to Default.",
    )
    ap.add_argument(
        "--strip-overrides",
        action="store_true",
        help="When --reskin, strip existing ASS override tags from event text before reapplying preset/animation.",
    )
    ap.add_argument(
        "--apply-animation",
        action="store_true",
        help="Inject preset animation tags. For SRT conversion this is also controlled by preset.animation.",
    )
    ap.add_argument(
        "--no-animation",
        action="store_true",
        help="Disable animation injection even if preset includes animation settings.",
    )
    ap.add_argument(
        "--fps",
        default="30",
        help="Overlay framerate (e.g., 30, 60, or 30000/1001). Default: 30",
    )
    ap.add_argument(
        "--safety-scale",
        type=float,
        default=1.12,
        help="Multiplier to avoid edge clipping vs measurement/render mismatch. Default: 1.12",
    )
    ap.add_argument(
        "--keep-ass",
        action="store_true",
        help="Save intermediate ASS file alongside output (same base name).",
    )
    ap.add_argument(
        "--loglevel",
        default="error",
        help="FFmpeg loglevel (quiet, error, warning, info). Default: error",
    )
    ap.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep temporary working directory for debugging.",
    )
    ap.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output (errors still print).",
    )
    ap.add_argument(
        "--hide-ffmpeg-progress",
        action="store_true",
        help="Hide FFmpeg render progress (frames/time).",
    )


    args = ap.parse_args()

    prog = Progress(enabled=not args.quiet)

    ffmpeg = which_or_die("ffmpeg")

    in_path = Path(args.input)
    if not in_path.exists():
        die(f"Input file '{in_path}' not found.")

    if args.out is None or str(args.out).strip() == "":
        out_path = in_path.with_suffix(".mov")
    else:
        out_path = Path(args.out)

    out_path.parent.mkdir(parents=True, exist_ok=True)

    ext = in_path.suffix.lower().lstrip(".")
    if ext not in ("srt", "ass"):
        die("Input must be .srt or .ass")

    prog.step(f"Input: {in_path.name}")
    prog.step(f"Output: {out_path.name}")

    preset: Optional[Dict[str, Any]] = None
    need_preset = (ext == "srt") or args.reskin or (not args.no_preset_for_ass and ext == "ass")
    if need_preset:
        preset = load_preset(args.preset)

    apply_animation = bool(args.apply_animation)
    if args.no_animation:
        apply_animation = False

    # For SRT conversion, if user didn't explicitly pass --apply-animation, we still apply
    # preset.animation unless --no-animation is set. This matches "preset-driven" behavior.
    if ext == "srt" and not args.apply_animation and not args.no_animation:
        apply_animation = True

    with tempfile.TemporaryDirectory(prefix="render_overlay_") as td:
        tdir = Path(td)
        ass_out = tdir / "work.ass"

        # Load subtitles
        try:
            subs = pysubs2.load(str(in_path))
            prog.step(f"Loaded subtitles: {len(subs.events)} events")
        except Exception as e:
            die(f"Failed to load subtitles: {e}")

        if ext == "srt":
            if preset is None:
                die("Preset is required for SRT inputs.")
            subs_ass = convert_srt_to_ass(subs, preset, apply_animation=apply_animation)
        else:
            # ASS input
            subs_ass = subs
            if args.reskin:
                if preset is None:
                    die("Preset is required when using --reskin on ASS inputs.")
                subs_ass = reskin_ass(
                    subs_ass,
                    preset,
                    strip_existing_overrides=bool(args.strip_overrides),
                    apply_animation=apply_animation,
                )
            else:
                # If preset is provided and user didn't disable it, we can still use it for sizing rules
                # ONLY if not --no-preset-for-ass; otherwise we size based on a default heuristic.
                if preset is None and not args.no_preset_for_ass:
                    preset = load_preset(args.preset)

        # If we still have no preset (ASS input + --no-preset-for-ass and no --reskin),
        # we must size somehow. We cannot measure reliably without a font spec.
        # In that mode, require user to pass a preset or reskin.
        if preset is None:
            die(
                "For .ass inputs, provide --preset (for sizing) or use --reskin. "
                "Otherwise sizing cannot be computed deterministically."
            )

        # Substitute slide animation placeholders if used
        size = compute_tight_overlay_size(subs_ass, preset, safety_scale=float(args.safety_scale))
        prog.step(f"Computed tight overlay size: {size.width}x{size.height}")

        # Write working ASS
        try:
            subs_ass.info["PlayResX"] = str(size.width)
            subs_ass.info["PlayResY"] = str(size.height)
            subs_ass.save(str(ass_out), format_="ass")
        except Exception as e:
            die(f"Failed to save intermediate ASS: {e}")

        substitute_slide_placeholders(ass_out, preset, size)

        end_ms = compute_subs_end_ms(subs_ass)
        duration_sec = (end_ms / 1000.0) + 0.25  # small pad for fade-out / safety
        prog.step(f"Subtitle length: {end_ms} ms  (~{duration_sec:.2f} s)")

        # Render overlay video
        prog.step("Rendering overlay video via FFmpeg...")
        run_ffmpeg_render_overlay(
            ffmpeg=ffmpeg,
            ass_path=ass_out,
            out_path=out_path,
            size=size,
            fps=str(args.fps),
            loglevel=str(args.loglevel),
            keep_temp=bool(args.keep_temp),
            duration_sec=duration_sec,
            show_progress=not (bool(args.quiet) or bool(args.hide_ffmpeg_progress)),
        )
        prog.step("FFmpeg render complete")

        # Optionally keep ASS
        if args.keep_ass:
            ass_final = out_path.with_suffix(".ass")
            shutil.copy2(ass_out, ass_final)
            eprint(f"Wrote: {ass_final}")

        if args.keep_temp:
            # If user asked to keep temp, copy it out next to output
            dbg_dir = out_path.parent / (out_path.stem + "_debug")
            if dbg_dir.exists():
                shutil.rmtree(dbg_dir)
            shutil.copytree(tdir, dbg_dir)
            eprint(f"Kept debug directory: {dbg_dir}")

    eprint(f"Overlay rendered: {out_path}")
    eprint(f"Overlay size: {size.width}x{size.height} @ {args.fps} fps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
