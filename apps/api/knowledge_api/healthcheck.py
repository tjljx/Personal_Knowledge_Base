"""Container healthcheck for the background worker.

Exits 0 only when the worker heartbeat is fresh, so `docker ps` shows
`unhealthy` instead of `Up` when the parsing pipeline has silently stopped.
"""

import argparse
import sys

from sqlalchemy.orm import Session

from knowledge_api.db import get_engine
from knowledge_api.worker import HEARTBEAT_TIMEOUT_SECONDS, heartbeat_age_seconds


def worker_exit_code(db: Session) -> int:
    age = heartbeat_age_seconds(db)
    if age is None:
        print("worker heartbeat missing: the worker has never completed a cycle")
        return 1
    if age > HEARTBEAT_TIMEOUT_SECONDS:
        print(f"worker heartbeat is {age:.0f}s old (limit {HEARTBEAT_TIMEOUT_SECONDS}s)")
        return 1
    print(f"worker heartbeat is {age:.0f}s old")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Report worker liveness for Docker")
    parser.parse_args()
    with Session(get_engine()) as db:
        return worker_exit_code(db)


if __name__ == "__main__":
    sys.exit(main())
