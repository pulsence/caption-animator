"""
FFmpeg-based video rendering.

This module provides a renderer that uses FFmpeg with libass to render
subtitle overlays as transparent ProRes 4444 videos.
"""

import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from ..core.sizing import OverlaySize
from .progress import ProgressTracker


class FFmpegRenderer:
    """
    Renders subtitle overlays using FFmpeg.

    This renderer creates transparent video files (ProRes 4444 with alpha channel)
    by rendering ASS subtitles onto a transparent canvas using FFmpeg's libass filter.

    Example:
        renderer = FFmpegRenderer(loglevel="error", show_progress=True)
        renderer.render(
            ass_path=Path("subtitles.ass"),
            output_path=Path("overlay.mov"),
            size=OverlaySize(1920, 1080),
            fps="30",
            duration_sec=120.5
        )
    """

    def __init__(
        self,
        loglevel: str = "error",
        show_progress: bool = True,
        ffmpeg_path: Optional[str] = None
    ):
        """
        Initialize FFmpeg renderer.

        Args:
            loglevel: FFmpeg log level (quiet, error, warning, info, debug)
            show_progress: Whether to show render progress
            ffmpeg_path: Path to ffmpeg binary (if None, searches PATH)
        """
        self.loglevel = loglevel
        self.show_progress = show_progress
        self.ffmpeg_path = ffmpeg_path or self._find_ffmpeg()

    def _find_ffmpeg(self) -> str:
        """
        Find FFmpeg executable on the system.

        Returns:
            Path to ffmpeg binary

        Raises:
            RuntimeError: If ffmpeg is not found
        """
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError(
                "FFmpeg not found on PATH. Please install FFmpeg and ensure "
                "it is available in your system PATH."
            )
        return ffmpeg

    def render(
        self,
        ass_path: Path,
        output_path: Path,
        size: OverlaySize,
        fps: str,
        duration_sec: float
    ) -> None:
        """
        Render ASS subtitles to transparent video.

        Args:
            ass_path: Path to ASS subtitle file
            output_path: Path for output video file (.mov)
            size: Overlay dimensions
            fps: Frame rate (e.g., "30", "60", "30000/1001")
            duration_sec: Video duration in seconds

        Raises:
            RuntimeError: If rendering fails
        """
        w, h = size.width, size.height

        # Escape path for FFmpeg filter syntax
        ass_escaped = self._escape_filter_path(ass_path)

        # Build filter chain
        video_filter = (
            f"format=rgba,"
            f"subtitles=filename='{ass_escaped}':alpha=1:original_size={w}x{h},"
            f"format=yuva444p10le"
        )

        # Build FFmpeg command
        cmd = [
            self.ffmpeg_path,
            "-y",  # Overwrite output
            "-hide_banner",
            "-loglevel", self.loglevel,
            "-f", "lavfi",
            "-t", f"{duration_sec:.3f}",
            "-i", f"color=c=black@0.0:s={w}x{h}:r={fps}",
            "-vf", video_filter,
            "-c:v", "prores_ks",
            "-profile:v", "4",  # ProRes 4444
            "-pix_fmt", "yuva444p10le",
            "-r", fps,
            "-an",  # No audio
            str(output_path),
        ]

        # Add progress reporting if requested
        if self.show_progress:
            cmd.insert(1, "-progress")
            cmd.insert(2, "pipe:2")
            cmd.insert(3, "-nostats")

        # Log command
        print("FFmpeg command:", file=sys.stderr)
        print("  " + " ".join(cmd), file=sys.stderr)

        # Execute
        if not self.show_progress:
            self._render_simple(cmd, output_path)
        else:
            self._render_with_progress(cmd, output_path)

    def _render_simple(self, cmd: list, output_path: Path) -> None:
        """Run FFmpeg without progress tracking."""
        result = subprocess.run(cmd)
        if result.returncode != 0:
            raise RuntimeError("FFmpeg render failed. Check output above for details.")

        self._verify_output(output_path)

    def _render_with_progress(self, cmd: list, output_path: Path) -> None:
        """Run FFmpeg with progress tracking."""
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

            key, value = line.split("=", 1)

            if key == "frame":
                frame = value
            elif key == "out_time":
                out_time = value
            elif key == "speed":
                speed = value
            elif key == "progress" and value == "end":
                break

            # Print progress at most twice per second
            now = time.time()
            if now - last_print >= 0.5 and (frame or out_time):
                msg = "FFmpeg"
                if frame:
                    msg += f" frame={frame}"
                if out_time:
                    msg += f" time={out_time}"
                if speed:
                    msg += f" speed={speed}"
                print(msg, file=sys.stderr)
                last_print = now

        returncode = proc.wait()
        if returncode != 0:
            raise RuntimeError("FFmpeg render failed. Check output above for details.")

        self._verify_output(output_path)

    def _verify_output(self, output_path: Path) -> None:
        """Verify that output file was created successfully."""
        if not output_path.exists():
            raise RuntimeError(f"Output file was not created: {output_path}")

        if output_path.stat().st_size < 1024:
            raise RuntimeError(
                f"Output file is too small ({output_path.stat().st_size} bytes), "
                "render likely failed"
            )

    @staticmethod
    def _escape_filter_path(path: Path) -> str:
        """
        Escape file path for FFmpeg filter syntax.

        FFmpeg filters use ':' as separators and need special escaping for:
        - Backslashes (Windows paths)
        - Colons (Windows drive letters)
        - Single quotes (used as delimiters)

        Args:
            path: Path to escape

        Returns:
            Escaped path string safe for FFmpeg filter
        """
        # Use forward slashes (works on all platforms)
        s = str(path.resolve()).replace("\\", "/")

        # Escape colons (for drive letters like C:)
        s = s.replace(":", r"\:")

        # Escape single quotes
        s = s.replace("'", r"\'")

        return s
