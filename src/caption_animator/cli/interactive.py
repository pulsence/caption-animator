"""
Interactive mode for tweaking presets and re-rendering.
"""

import json
import sys
from pathlib import Path
from typing import Any

from ..core.config import PresetConfig
from ..presets.loader import PresetLoader
from .main import render_subtitle


def interactive_mode(args, input_path: Path, output_path: Path) -> int:
    """
    Interactive REPL for tweaking preset settings and re-rendering.

    Args:
        args: Parsed command-line arguments
        input_path: Input subtitle file
        output_path: Output video file

    Returns:
        Exit code
    """
    print("\nInteractive mode. Type 'help' for commands.\n", file=sys.stderr)

    # Load initial preset
    loader = PresetLoader()
    preset = loader.load(args.preset)
    baseline_preset = PresetConfig.from_dict(preset.to_dict())  # Keep original

    def print_preset_summary():
        """Print current preset configuration."""
        print("Current preset/settings:", file=sys.stderr)
        print(f"  out          : {output_path}", file=sys.stderr)
        print(f"  fps          : {args.fps}", file=sys.stderr)
        print(f"  safety_scale : {args.safety_scale}", file=sys.stderr)
        print(f"  font         : {preset.font_name} size={preset.font_size} bold={preset.bold}", file=sys.stderr)
        print(f"  colors       : primary={preset.primary_color} outline={preset.outline_color}", file=sys.stderr)
        print(f"  outline/shadow: outline_px={preset.outline_px} shadow_px={preset.shadow_px}", file=sys.stderr)
        print(f"  wrap         : max_width_px={preset.max_width_px} line_spacing={preset.line_spacing}", file=sys.stderr)
        if preset.animation:
            print(f"  animation    : type={preset.animation.type} params={preset.animation.params}", file=sys.stderr)

    def do_render():
        """Perform rendering with current settings."""
        try:
            render_subtitle(input_path, output_path, preset, args)
        except Exception as e:
            print(f"Render failed: {e}", file=sys.stderr)

    def set_value(key: str, value_str: str):
        """Set a preset value by dotted key path."""
        # Parse value
        value: Any = value_str.strip()

        # Try to parse as JSON for complex types
        if value.lower() in ("true", "false"):
            value = value.lower() == "true"
        elif value.lower() in ("none", "null"):
            value = None
        else:
            try:
                value = int(value)
            except ValueError:
                try:
                    value = float(value)
                except ValueError:
                    # Try JSON parsing for lists/dicts
                    if value.startswith(("[", "{")):
                        try:
                            value = json.loads(value)
                        except:
                            pass  # Keep as string

        # Set the value
        parts = key.split(".")
        if len(parts) == 1:
            # Top-level attribute
            if hasattr(preset, key):
                setattr(preset, key, value)
                print(f"{key} = {getattr(preset, key)}", file=sys.stderr)
            else:
                print(f"Unknown key: {key}", file=sys.stderr)
        elif parts[0] == "animation" and len(parts) == 2:
            # Animation parameter
            if preset.animation:
                preset.animation.params[parts[1]] = value
                print(f"animation.{parts[1]} = {value}", file=sys.stderr)
            else:
                print("No animation configured", file=sys.stderr)
        else:
            print(f"Unsupported nested key: {key}", file=sys.stderr)

    # Main loop
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.", file=sys.stderr)
            return 0

        if not line:
            continue

        parts = line.split(None, 1)
        cmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        if cmd in ("q", "quit", "exit"):
            return 0

        elif cmd in ("h", "help", "?"):
            print(
                "Commands:\n"
                "  r | render                 Render with current settings\n"
                "  p | print                  Print preset/settings summary\n"
                "  set <key> <value>          Set preset value (e.g., set font_size 72)\n"
                "  out <path>                 Change output path\n"
                "  fps <value>                Change FPS\n"
                "  scale <value>              Change safety_scale\n"
                "  save <path.json>           Save current preset as JSON\n"
                "  reset                      Reset preset to initial state\n"
                "  quit                       Exit\n",
                file=sys.stderr
            )

        elif cmd in ("r", "render"):
            do_render()

        elif cmd in ("p", "print"):
            print_preset_summary()

        elif cmd == "reset":
            preset = PresetConfig.from_dict(baseline_preset.to_dict())
            print("Preset reset to initial state.", file=sys.stderr)

        elif cmd == "save":
            if not rest:
                print("Usage: save <path.json>", file=sys.stderr)
                continue
            save_path = Path(rest.strip())
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text(preset.to_json(), encoding="utf-8")
            print(f"Saved preset: {save_path}", file=sys.stderr)

        elif cmd == "out":
            if not rest:
                print("Usage: out <path.mov>", file=sys.stderr)
                continue
            output_path = Path(rest.strip())
            output_path.parent.mkdir(parents=True, exist_ok=True)
            print(f"Output set: {output_path}", file=sys.stderr)

        elif cmd == "fps":
            if not rest:
                print("Usage: fps <value>", file=sys.stderr)
                continue
            args.fps = rest.strip()
            print(f"FPS set: {args.fps}", file=sys.stderr)

        elif cmd == "scale":
            if not rest:
                print("Usage: scale <value>", file=sys.stderr)
                continue
            args.safety_scale = float(rest.strip())
            print(f"safety_scale set: {args.safety_scale}", file=sys.stderr)

        elif cmd == "set":
            if not rest or " " not in rest:
                print("Usage: set <key> <value>", file=sys.stderr)
                continue
            key, value = rest.split(None, 1)
            set_value(key, value)

        else:
            print(f"Unknown command: {cmd}. Type 'help' for commands.", file=sys.stderr)
