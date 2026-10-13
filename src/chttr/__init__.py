"""chttr: Wispr Flow-style voice dictation for Omarchy / Hyprland."""

__version__ = "1.0.0rc1"


def main() -> None:
    from .cli import main as _main

    _main()
