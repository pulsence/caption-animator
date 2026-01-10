"""
Main CLI entry point.
"""

import shutil
import sys
import tempfile
from pathlib import Path

from ..core.config import PresetConfig
from ..core.subtitle import SubtitleFile
from ..core.style import StyleBuilder
from ..core.sizing import SizeCalculator
from ..animations import AnimationRegistry
from ..presets.loader import PresetLoader
from ..rendering.ffmpeg import FFmpegRenderer
from ..rendering.progress import ProgressTracker
from .args import parse_args
from .commands import list_presets_command


def render_subtitle(
    input_path: Path,
    output_path: Path,
    preset: PresetConfig,
    args
) -> None:
    """
    Render subtitle file to video overlay.

    Args:
        input_path: Input subtitle file
        output_path: Output video file
        preset: Preset configuration
        args: Parsed command-line arguments
    """
    progress = ProgressTracker(enabled=not args.quiet)

    # Determine source format
    ext = input_path.suffix.lower().lstrip(".")
    if ext not in ("srt", "ass"):
        raise ValueError(f"Unsupported format: {ext}. Use .srt or .ass")

    progress.step(f"Input: {input_path.name}")
    progress.step(f"Output: {output_path.name}")

    # Load subtitle file
    progress.step(f"Loading subtitles: {len(input_path.read_text().splitlines())} lines")
    subtitle = SubtitleFile.load(input_path)
    progress.step(f"Loaded {len(subtitle.subs.events)} subtitle events")

    # Determine if we should apply animation
    apply_animation = args.apply_animation
    if args.no_animation:
        apply_animation = False
    elif ext == "srt" and not args.apply_animation and not args.no_animation:
        # Auto-apply for SRT
        apply_animation = True

    # Create temp directory
    with tempfile.TemporaryDirectory(prefix="caption_animator_") as temp_dir:
        temp_path = Path(temp_dir)
        ass_path = temp_path / "work.ass"

        # Build and apply style
        progress.step("Building ASS style from preset...")
        style_builder = StyleBuilder(preset)
        style = style_builder.build("Default")

        # For SRT: always apply style and wrap
        # For ASS with --reskin: apply style and wrap
        # For ASS without --reskin: just copy
        if ext == "srt" or args.reskin:
            subtitle.apply_style(style, preset, wrap_text=True)
        else:
            # Copy ASS as-is, but we still need preset for sizing
            pass

        # Apply animation if requested
        if apply_animation and preset.animation:
            progress.step(f"Applying animation: {preset.animation.type}")
            animation = AnimationRegistry.create(
                preset.animation.type,
                preset.animation.params
            )
            subtitle.apply_animation(animation)

        # Calculate overlay size
        progress.step("Computing tight overlay size...")
        size_calc = SizeCalculator(preset, safety_scale=args.safety_scale)
        size = size_calc.compute_size(subtitle.subs)
        progress.step(f"Computed overlay size: {size.width}x{size.height}")

        # Apply center positioning
        position = size_calc.compute_anchor_position(size)
        subtitle.apply_center_positioning(position, size)

        # Set play resolution
        subtitle.set_play_resolution(size)

        # Save ASS file
        subtitle.save(ass_path)

        # Handle placeholder substitution for slide_up
        if apply_animation and preset.animation and preset.animation.type == "slide_up":
            animation = AnimationRegistry.create(
                preset.animation.type,
                preset.animation.params
            )
            if animation.supports_placeholder_substitution():
                content = ass_path.read_text(encoding="utf-8")
                content = animation.substitute_placeholders(content, position)
                ass_path.write_text(content, encoding="utf-8")

        # Calculate duration
        end_ms = subtitle.get_duration_ms()
        duration_sec = (end_ms / 1000.0) + 0.25  # Add small pad
        progress.step(f"Subtitle duration: {end_ms}ms (~{duration_sec:.2f}s)")

        # Render with FFmpeg
        progress.step("Rendering overlay video via FFmpeg...")
        renderer = FFmpegRenderer(
            loglevel=args.loglevel,
            show_progress=not (args.quiet or args.hide_ffmpeg_progress)
        )

        renderer.render(
            ass_path=ass_path,
            output_path=output_path,
            size=size,
            fps=args.fps,
            duration_sec=duration_sec
        )

        progress.step("FFmpeg render complete")

        # Save ASS if requested
        if args.keep_ass:
            ass_final = output_path.with_suffix(".ass")
            shutil.copy2(ass_path, ass_final)
            print(f"Saved ASS: {ass_final}", file=sys.stderr)

        # Keep temp directory if requested
        if args.keep_temp:
            debug_dir = output_path.parent / (output_path.stem + "_debug")
            if debug_dir.exists():
                shutil.rmtree(debug_dir)
            shutil.copytree(temp_path, debug_dir)
            print(f"Kept debug directory: {debug_dir}", file=sys.stderr)

    print(f"Overlay rendered: {output_path}", file=sys.stderr)
    print(f"Overlay size: {size.width}x{size.height} @ {args.fps} fps", file=sys.stderr)


def main(argv=None) -> int:
    """
    Main CLI entry point.

    Args:
        argv: Command-line arguments (default: sys.argv[1:])

    Returns:
        Exit code (0 for success, non-zero for error)
    """
    try:
        args = parse_args(argv)

        # Handle --list-presets
        if args.list_presets:
            return list_presets_command()

        # Validate input file
        input_path = Path(args.input)
        if not input_path.exists():
            print(f"ERROR: Input file not found: {input_path}", file=sys.stderr)
            return 2

        # Determine output path
        if args.out:
            output_path = Path(args.out)
        else:
            output_path = input_path.with_suffix(".mov")

        # Create output directory if needed
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Handle interactive mode
        if args.interactive:
            from .interactive import interactive_mode
            return interactive_mode(args, input_path, output_path)

        # Load preset
        loader = PresetLoader()
        preset = loader.load(args.preset)

        # Render
        render_subtitle(input_path, output_path, preset, args)

        return 0

    except KeyboardInterrupt:
        print("\nInterrupted by user", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        if not (hasattr(args, 'quiet') and args.quiet):
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
