#!/usr/bin/env python3
import sys

try:
    from .cli import main
except (ImportError, ValueError):
    from cli import main

if __name__ == "__main__":
    sys.exit(main())
