import threading
from concurrent.futures import ThreadPoolExecutor

import boto3

from cloudprivs.providers.aws.service import Service


class FakeClient:
    """
    A lightweight stand-in for a boto3 client, just enough to exercise
    test_all_operations/scan without touching AWS: a matching method per operation
    name, and the .meta.region_name attribute test_permission/test_all_operations use.
    """

    class _Meta:
        def __init__(self, region_name):
            self.region_name = region_name

    def __init__(self, region_name, operations):
        self.meta = FakeClient._Meta(region_name)
        for op in operations:
            setattr(self, op, lambda *a, **kw: {"ok": True})


def make_service_with_fake_clients(num_regions: int, num_operations: int, max_workers: int):
    session = boto3.Session(
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    svc = Service(
        "s3", session, executor=ThreadPoolExecutor(max_workers=max_workers), regions=["us-east-1"]
    )
    operations = [f"op_{i}" for i in range(num_operations)]
    svc.operations = operations
    svc.dry_run_operations = set()
    svc.injected_args = {}
    svc.clients = [
        FakeClient(f"region-{i}", operations) for i in range(num_regions)
    ]
    return svc


class TestScanDoesNotDeadlock:
    """
    Regression coverage for a real deadlock: scan() used to submit one
    test_all_operations task per region to self.executor, then block waiting for
    them - but test_all_operations submits its own per-operation tasks to that same
    executor and also blocks waiting. Once the number of regions reached
    max_workers, every worker thread ended up occupied by a region-level task
    blocked on operation-level tasks that could never get a free thread to run on
    (hit in the wild: S3's 34 regions vs the CLI's MAX_WORKERS=30).

    Reproduced here at a small scale (3 regions against a 2-worker pool) so the test
    is fast and deterministic rather than needing real region/worker counts.
    """

    def test_scan_completes_when_regions_exceed_worker_count(self):
        svc = make_service_with_fake_clients(
            num_regions=3, num_operations=5, max_workers=2
        )

        result_holder = {}

        def run():
            result_holder["result"] = svc.scan()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(timeout=10)

        assert not thread.is_alive(), "scan() deadlocked instead of completing"
        assert len(result_holder["result"]) == 5  # one entry per operation
