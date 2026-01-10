"""
Caption Animator - Subtitle overlay rendering with animated effects.

A modern Python package for rendering stylized subtitle overlays as transparent
video files for use in video editing software like DaVinci Resolve.

Main features:
- Plugin-based animation system
- Type-safe preset configuration
- Multiple animation types (fade, slide, scale, blur, word-reveal)
- SRT and ASS format support
- Transparent ProRes 4444 video output

Example:
    from caption_animator import SubtitleFile, PresetLoader, AnimationRegistry
    from caption_animator.rendering import FFmpegRenderer
    from caption_animator.core import SizeCalculator

    # Load subtitle and preset
    sub = SubtitleFile.load("input.srt")
    preset = PresetLoader().load("modern_box")

    # Apply animation
    animation = AnimationRegistry.create("fade", preset.animation.params)
    sub.apply_animation(animation)

    # Render
    size = SizeCalculator(preset).compute_size(sub.subs)
    renderer = FFmpegRenderer()
    renderer.render(ass_path, output_path, size, fps="30", duration_sec=120)
"""

__version__ = "0.1.0"
__author__ = "Timothy Eck"
__license__ = "MIT"

# Core imports
from .core.config import PresetConfig, AnimationConfig
from .core.subtitle import SubtitleFile
from .core.sizing import OverlaySize, SizeCalculator
from .core.style import StyleBuilder

# Animation system
from .animations import (
    BaseAnimation,
    AnimationRegistry,
    FadeAnimation,
    SlideUpAnimation,
    ScaleSettleAnimation,
    BlurSettleAnimation,
    WordRevealAnimation,
)

# Preset system
from .presets.loader import PresetLoader
from .presets.defaults import get_builtin_preset, list_builtin_presets

# Rendering
from .rendering.ffmpeg import FFmpegRenderer
from .rendering.progress import ProgressTracker

__all__ = [
    # Version info
    "__version__",
    "__author__",
    "__license__",
    # Core
    "PresetConfig",
    "AnimationConfig",
    "SubtitleFile",
    "OverlaySize",
    "SizeCalculator",
    "StyleBuilder",
    # Animations
    "BaseAnimation",
    "AnimationRegistry",
    "FadeAnimation",
    "SlideUpAnimation",
    "ScaleSettleAnimation",
    "BlurSettleAnimation",
    "WordRevealAnimation",
    # Presets
    "PresetLoader",
    "get_builtin_preset",
    "list_builtin_presets",
    # Rendering
    "FFmpegRenderer",
    "ProgressTracker",
]
