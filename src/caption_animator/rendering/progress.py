"""
Progress tracking utilities.

This module provides simple progress tracking for long-running operations.
"""

import sys
import time
from typing import TextIO


class ProgressTracker:
    """
    Simple progress tracker that logs timestamped messages.

    Example:
        progress = ProgressTracker(enabled=True)
        progress.step("Loading subtitles...")
        progress.step("Rendering video...")
    """

    def __init__(self, enabled: bool = True, output: TextIO = sys.stderr):
        """
        Initialize progress tracker.

        Args:
            enabled: Whether to actually print progress messages
            output: File-like object to write to (default: stderr)
        """
        self.enabled = enabled
        self.output = output
        self._start_time = time.time()

    def step(self, message: str) -> None:
        """
        Log a progress step with elapsed time.

        Args:
            message: Progress message to display
        """
        if not self.enabled:
            return

        elapsed = time.time() - self._start_time
        print(f"[{elapsed:6.1f}s] {message}", file=self.output)

    def reset(self) -> None:
        """Reset the timer to current time."""
        self._start_time = time.time()
