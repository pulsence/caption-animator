# Caption Animator

Render stylized subtitle overlays (SRT/ASS) into a transparent video (ProRes 4444 with alpha) for DaVinci Resolve. The tool sizes a tight overlay canvas based on the maximum caption bounds, then renders the subtitles via FFmpeg/libass.

## Caveat Emptor
This project was primarily created for my personal use. I will not be responding to pull requests or issues unless they directly impact my use cases.

I generated this tool primarily using an AI code assistant and so all the code branches are not
explored or tested, but they should be farely correct.

## Features
- SRT and ASS inputs (ASS is preserved by default).
- Preset-driven styling (JSON/YAML) for SRT conversion and optional ASS reskin.
- Optional line-level animations (fade, slide_up, scale_settle, blur_settle).
- Tight overlay sizing to keep the output video as small as possible.
- ProRes 4444 MOV output with alpha for easy positioning in Resolve.

## Requirements
- Python 3
- FFmpeg available on PATH
- Python packages: `pysubs2`, `pillow`, and `pyyaml` (only needed for YAML presets)

Install Python deps:
```bash
pip install pysubs2 pillow pyyaml
```

## Quick Start
Render from SRT using a local preset file:
```bash
python cli.py test.srt --preset presets/preset.json --out overlay.mov
```

Render from ASS (keeps existing styling unless you reskin):
```bash
python cli.py test.ass --out overlay.mov
```

Reskin an ASS with a preset and strip existing overrides:
```bash
python cli.py test.ass --preset presets/preset.json --reskin --strip-overrides --out overlay.mov
```

Keep the intermediate ASS file:
```bash
python cli.py test.srt --preset presets/preset.json --keep-ass
```

## Presets and Animation Configuration
Presets define fonts, colors, layout, and animations. They can be:
- A built-in preset name: `modern_box` or `clean_outline`
- A single JSON/YAML preset file
- A multi-preset JSON/YAML file addressed as `path/to/presets.json:preset_name`

Animation settings live under the `animation` key in a preset file, for example in `presets/preset.json`. The built-in preset definitions (including animation fields and defaults) are in `cli.py` under `BUILTIN_PRESETS`.

See [Libass](https://github.com/libass/libass?tab=readme-ov-file) for more documentation on ASS styling.

### Example preset (JSON)
```json
{
  "font_file": "C:/Windows/Fonts/arialbd.ttf",
  "font_name": "Arial",
  "font_size": 62,
  "primary_color": "#FFFFFF",
  "outline_color": "#000000",
  "outline_px": 6,
  "padding": [44, 70, 56, 70],
  "animation": {
    "type": "slide_up",
    "in_ms": 140,
    "out_ms": 120,
    "move_px": 26
  }
}
```

Supported animation types:
- `fade` (uses `in_ms`, `out_ms`)
- `slide_up` (uses `in_ms`, `out_ms`, `move_px`)
- `scale_settle` (uses `in_ms`, `out_ms`, `start_scale`, `end_scale`, `accel`)
- `blur_settle` (uses `in_ms`, `out_ms`, `start_blur`, `end_blur`, `accel`)

## Notes
- For deterministic sizing, prefer presets with `font_file` pointing to a TTF/OTF file.
- ASS inputs require a preset for sizing unless you use `--reskin` or explicitly pass `--preset`.
- The overlay size is computed once per subtitle file; use `--safety-scale` if you see edge clipping.

## License
See `LICENSE`.
