"""
Quick test to verify animation system works.

Run with: python test_animations_basic.py
"""

import sys

sys.path.insert(0, "src")

from caption_animator.animations import (
    AnimationRegistry,
    FadeAnimation,
    SlideUpAnimation,
    ScaleSettleAnimation,
    BlurSettleAnimation,
    WordRevealAnimation,
    list_animations
)

def test_registry():
    """Test that all animations are registered."""
    print("Testing animation registry...")
    anims = list_animations()
    print(f"Registered animations: {anims}")

    expected = {"fade", "slide_up", "scale_settle", "blur_settle", "word_reveal"}
    assert set(anims) == expected, f"Expected {expected}, got {set(anims)}"
    print("[OK] All animations registered correctly")

def test_fade():
    """Test FadeAnimation creation and override generation."""
    print("\nTesting FadeAnimation...")
    fade = AnimationRegistry.create("fade", {"in_ms": 100, "out_ms": 200})
    override = fade.generate_ass_override()
    assert override == r"\fad(100,200)", f"Expected '\\fad(100,200)', got '{override}'"
    print(f"[OK] Fade override: {override}")

def test_slide():
    """Test SlideUpAnimation."""
    print("\nTesting SlideUpAnimation...")
    slide = AnimationRegistry.create("slide_up", {"in_ms": 140, "out_ms": 120, "move_px": 26})
    override = slide.generate_ass_override()
    assert "{X}" in override and "{Y}" in override
    print(f"[OK] Slide override (with placeholders): {override}")

    # Test placeholder substitution
    substituted = slide.substitute_placeholders(override, (100, 200))
    assert "{X}" not in substituted and "{Y}" not in substituted
    assert "100" in substituted and "226" in substituted  # Y + move_px
    print(f"[OK] Substituted: {substituted}")

def test_word_reveal():
    """Test WordRevealAnimation."""
    print("\nTesting WordRevealAnimation...")
    reveal = AnimationRegistry.create("word_reveal", {
        "mode": "even",
        "lead_in_ms": 0,
        "min_word_ms": 60,
        "max_word_ms": 400,
        "punct_pause_ms": 120
    })

    text = reveal._build_karaoke_text("Hello world!", 2000)
    assert r"\k" in text, "Expected \\k tags in karaoke text"
    print(f"[OK] Karaoke text generated: {text[:50]}...")

def test_info():
    """Test animation info retrieval."""
    print("\nTesting animation info...")
    info = AnimationRegistry.get_info()
    assert "fade" in info
    assert "default_params" in info["fade"]
    print(f"[OK] Animation info available for {len(info)} animations")

if __name__ == "__main__":
    try:
        test_registry()
        test_fade()
        test_slide()
        test_word_reveal()
        test_info()
        print("\n[SUCCESS] All animation tests passed!")
    except Exception as e:
        print(f"\n[FAILED] Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
