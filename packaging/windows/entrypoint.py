"""PyInstaller entry point with Windows multiprocessing initialization."""

from multiprocessing import freeze_support

from kicad_flow.server import main

if __name__ == "__main__":
    freeze_support()
    main()
