#!/usr/bin/env python
"""入口：python run.py serve|login|status|peer|send|tail"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cli import main

if __name__ == "__main__":
    main(sys.argv[1:])
