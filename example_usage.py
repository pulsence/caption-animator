"""
Example: Using the refactored Caption Animator as a library.

This demonstrates how to use the new modular architecture programmatically
without relying on the CLI.
"""

import sys
sys.path.insert(0, "src")

from pathlib import Path
from caption_animator import (
    SubtitleFile,
    PresetLoader,
    AnimationRegistry,
    SizeCalculator,
    StyleBuilder,
)
from caption_animator.rendering import FFmpegRenderer, ProgressTracker

def render_subtitle_overlay(
    input_file: Path,
    output_file: Path,
    preset_name: str = "modern_box",
    fps: str = "30",
    safety_scale: float = 1.12
):
    """
    Render a subtitle file as a transparent video overlay.

    Args:
        input_file: Path to .srt or .ass file
        output_file: Path for output .mov file
        preset_name: Name of preset to use
        fps: Frame rate
        safety_scale: Safety scaling factor for overlay size
    """
    progress = ProgressTracker(enabled=True)

    # 1. Load subtitle file
    progress.step(f"Loading subtitle: {input_file}")
    subtitle = SubtitleFile.load(input_file)

    # 2. Load preset
    progress.step(f"Loading preset: {preset_name}")
    loader = PresetLoader()
    preset = loader.load(preset_name)

    # 3. Build and apply style
    progress.step("Building ASS style...")
    style_builder = StyleBuilder(preset)
    style = style_builder.build("Default")
    subtitle.apply_style(style, preset, wrap_text=True)

    # 4. Apply animation
    if preset.animation:
        progress.step(f"Applying animation: {preset.animation.type}")
        animation = AnimationRegistry.create(
            preset.animation.type,
            preset.animation.params
        )
        subtitle.apply_animation(animation)

    # 5. Calculate overlay size
    progress.step("Calculating overlay dimensions...")
    size_calc = SizeCalculator(preset, safety_scale=safety_scale)
    size = size_calc.compute_size(subtitle.subs)
    progress.step(f"Overlay size: {size.width}x{size.height}")

    # 6. Apply center positioning
    position = size_calc.compute_anchor_position(size)
    subtitle.apply_center_positioning(position, size)

    # 7. Set play resolution and save temporary ASS
    subtitle.set_play_resolution(size)
    temp_ass = output_file.with_suffix(".temp.ass")
    subtitle.save(temp_ass)
    progress.step(f"Saved ASS: {temp_ass}")

    # 8. Handle placeholder substitution for slide animations
    if preset.animation and preset.animation.type == "slide_up":
        progress.step("Substituting animation placeholders...")
        animation = AnimationRegistry.create(
            preset.animation.type,
            preset.animation.params
        )
        if animation.supports_placeholder_substitution():
            # Read, substitute, write back
            content = temp_ass.read_text()
            content = animation.substitute_placeholders(content, position)
            temp_ass.write_text(content)

    # 9. Render with FFmpeg
    duration_sec = subtitle.get_duration_ms() / 1000.0 + 0.25
    progress.step(f"Rendering video ({duration_sec:.2f}s)...")

    renderer = FFmpegRenderer(loglevel="error", show_progress=True)
    renderer.render(
        ass_path=temp_ass,
        output_path=output_file,
        size=size,
        fps=fps,
        duration_sec=duration_sec
    )

    # 10. Cleanup
    temp_ass.unlink()
    progress.step(f"Complete! Output: {output_file}")

    return size


def main():
    """Example usage."""
    # Check if test.srt exists
    input_file = Path("test.srt")
    if not input_file.exists():
        print(f"Error: {input_file} not found")
        print("\nThis example requires test.srt to be present.")
        print("Create one or modify the script to use your own subtitle file.")
        return 1

    output_file = Path("example_output.mov")

    print("="*60)
    print("Caption Animator - Programmatic Example")
    print("="*60)

    try:
        size = render_subtitle_overlay(
            input_file=input_file,
            output_file=output_file,
            preset_name="modern_box",
            fps="30",
            safety_scale=1.12
        )

        print("\n" + "="*60)
        print("SUCCESS!")
        print("="*60)
        print(f"Input:  {input_file}")
        print(f"Output: {output_file}")
        print(f"Size:   {size.width}x{size.height}")
        print("\nYou can now import this video overlay into DaVinci Resolve")
        print("or any video editor that supports transparent video.")

    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
