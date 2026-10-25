"""Launcher adds discovered App queues without editing each deployment script."""

import argparse

from .registry import package_queues


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--queues", default="")
    parser.add_argument("--exclude-queues", default="")
    parser.add_argument("--packages-only", action="store_true")
    args, remaining = parser.parse_known_args()
    queues = sorted(set(filter(None, args.queues.split(","))) | set(package_queues()))
    queues = [q for q in queues if q not in set(args.exclude_queues.split(","))]
    if not queues:
        parser.error("At least one worker queue is required")
    if args.packages_only:
        from .package_celery import app as celery
    else:
        from src.core.celery_tasks.celery_app import celery
    celery.worker_main(["worker", "-Q", ",".join(queues), *remaining])


if __name__ == "__main__":
    main()
