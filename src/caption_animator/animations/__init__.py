"""
Animation plugin system with auto-discovery.

To create a new animation:
1. Create a new file in this directory (e.g., my_animation.py)
2. Subclass BaseAnimation
3. Decorate with @AnimationRegistry.register
4. Define animation_type class variable
5. Implement required methods (validate_params, generate_ass_override, apply_to_event)

The animation will be automatically discovered and available.

Example:
    from caption_animator.animations import AnimationRegistry

    # List available animations
    print(AnimationRegistry.list_types())

    # Create an animation
    fade = AnimationRegistry.create("fade", {"in_ms": 120, "out_ms": 120})

    # Apply to an event
    fade.apply_to_event(subtitle_event)
"""

from .base import BaseAnimation
from .registry import AnimationRegistry

# Import all animation modules to trigger registration
from .fade import FadeAnimation
from .slide import SlideUpAnimation
from .scale import ScaleSettleAnimation
from .blur import BlurSettleAnimation
from .word_reveal import WordRevealAnimation

# Export public API
__all__ = [
    "BaseAnimation",
    "AnimationRegistry",
    "FadeAnimation",
    "SlideUpAnimation",
    "ScaleSettleAnimation",
    "BlurSettleAnimation",
    "WordRevealAnimation",
]


def list_animations():
    """Convenience function to list all registered animations."""
    return AnimationRegistry.list_types()


def get_animation_info():
    """Get information about all registered animations."""
    return AnimationRegistry.get_info()
