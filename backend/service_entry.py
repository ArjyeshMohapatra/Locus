import argparse
import os
import sys
from pathlib import Path

import uvicorn


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run LOCUS backend in service mode")
    parser.add_argument("--host", default=os.getenv("LOCUS_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("LOCUS_PORT", "8000")),
    )
    parser.add_argument(
        "--data-dir",
        default=os.getenv("LOCUS_DATA_DIR", ""),
        help="Optional data directory for LOCUS DB and snapshot files",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    backend_dir = Path(__file__).resolve().parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    if args.data_dir:
        os.environ["LOCUS_DATA_DIR"] = args.data_dir

    os.environ["LOCUS_HOST"] = str(args.host)
    os.environ["LOCUS_PORT"] = str(args.port)

    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
