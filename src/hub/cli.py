import sys


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "worker":
        from hub.worker import main as run
    elif command == "scheduler":
        from hub.scheduler import main as run
    elif command == "seed":
        from hub.seed import main as run
    else:
        raise SystemExit("usage: hub worker|scheduler|seed")
    run()
