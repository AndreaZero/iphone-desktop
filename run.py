"""Application and frozen worker entry point."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == "--bridge":
        from iphone_desktop.bridge import main
        main(sys.argv[2:])
    else:
        from iphone_desktop.app import main
        main()
