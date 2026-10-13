"""chttr: Wispr Flow-style voice dictation for Omarchy / Hyprland."""

__version__ = "0.8.0"


def main() -> None:
    from .cli import main as _main

    _main()
