#!/usr/bin/python3
"""Fixed launcher for the installed UIF native publisher."""
import sys

from uif.native_publisher import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
