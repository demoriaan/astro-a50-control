"""Entry point: GUI by default, command line for the documented subcommands."""
import sys

CLI_ARGS = {"status", "preset", "slider", "noise-gate", "mic-eq", "save", "-h", "--help", "--device"}


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in CLI_ARGS:
        from .cli import main as run
    else:
        from .app import main as run
    return run()


if __name__ == "__main__":
    sys.exit(main())
