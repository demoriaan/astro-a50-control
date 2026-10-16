import sys

CLI_ARGS = {"status", "preset", "slider", "noise-gate", "mic-eq", "save", "-h", "--help", "--device"}

if len(sys.argv) > 1 and sys.argv[1] in CLI_ARGS:
    from .cli import main
else:
    from .app import main

sys.exit(main())
