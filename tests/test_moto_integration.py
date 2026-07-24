import yaml
from moto import mock_aws

from cloudprivs.providers.aws.service import TESTS_LOCATION, Service
from cloudprivs.providers.aws.service import TestStatus as OpStatus


def load_real_custom_tests():
    with open(TESTS_LOCATION) as h:
        return yaml.safe_load(h)


@mock_aws
def test_dry_run_operation_reports_succeeded_via_moto(fake_session):
    """
    End-to-end check that the DryRun trick actually works against something that
    behaves like real AWS: moto's EC2 backend implements DryRun the same way AWS does
    (DryRunOperation / HTTP 412), so a forced DryRun=True call through
    Service.test_permission should classify as SUCCEEDED without creating anything.
    """
    injected_args = load_real_custom_tests()
    svc = Service(
        "ec2",
        fake_session,
        executor=None,
        regions=["us-east-1"],
        injected_args=injected_args,
    )
    assert "allocate_address" in svc.dry_run_operations

    client = svc.clients[0]
    args, kwargs = svc._get_custom_args("allocate_address")
    kwargs["DryRun"] = True
    result = svc.test_permission("allocate_address", client, *args, **kwargs)

    assert result.status == OpStatus.SUCCEEDED
    assert result.error is not None  # we got the DryRunOperation error, not real data
    assert client.describe_addresses()["Addresses"] == []  # nothing was allocated


@mock_aws
def test_full_ec2_scan_creates_no_resources(fake_session):
    """
    Run test_all_operations for a single EC2 client - covering every prefix-matched
    read op plus every seeded dry-run op in CustomTests.yaml - and confirm nothing
    observable was mutated. This is the regression test for the original Shield
    incident: brute-forcing every operation in scope must never leave a trace.
    """
    injected_args = load_real_custom_tests()
    svc = Service(
        "ec2",
        fake_session,
        executor=None,
        regions=["us-east-1"],
        injected_args=injected_args,
    )
    assert svc.dry_run_operations  # sanity: we actually expanded scope

    results = svc.test_all_operations(svc.clients[0])
    # moto doesn't implement every EC2 action, so fewer results than operations tested
    # is expected here - that's a moto coverage gap, not a signal about our code.
    assert 0 < len(results) <= len(svc.operations)

    # every dry-run operation that moto *did* implement must have actually gone
    # through the forced DryRun path, i.e. it never ERRORED with a ParamValidationError
    # for a missing DryRun-unrelated arg (proving DryRun=True made it through) and
    # never actually SUCCEEDED by returning real created-resource data
    by_name = {r.name: r for r in results}
    tested_dry_run_ops = [op for op in svc.dry_run_operations if op in by_name]
    assert tested_dry_run_ops  # sanity: moto implemented at least some of them
    for op in tested_dry_run_ops:
        assert by_name[op].status in (OpStatus.SUCCEEDED, OpStatus.FAILED)

    assert svc.clients[0].describe_addresses()["Addresses"] == []
    assert svc.clients[0].describe_key_pairs()["KeyPairs"] == []
    assert svc.clients[0].describe_vpcs()["Vpcs"][0]["IsDefault"] is True  # only the default VPC


@mock_aws
def test_describe_operations_still_return_real_data(fake_session):
    """
    Regression guard: expanding scope with dry-run operations must not affect the
    existing get_/list_/describe_ operations, which should still return real results
    rather than being forced through DryRun.
    """
    injected_args = load_real_custom_tests()
    client_for_setup = fake_session.client("ec2", region_name="us-east-1")
    client_for_setup.create_key_pair(KeyName="test-key")

    svc = Service(
        "ec2",
        fake_session,
        executor=None,
        regions=["us-east-1"],
        injected_args=injected_args,
    )
    client = svc.clients[0]
    result = svc.test_permission("describe_key_pairs", client)

    assert result.status == OpStatus.SUCCEEDED
    assert result.results["KeyPairs"][0]["KeyName"] == "test-key"


@mock_aws
def test_s3_list_operations_succeed(fake_session):
    injected_args = load_real_custom_tests()
    setup_client = fake_session.client("s3", region_name="us-east-1")
    setup_client.create_bucket(Bucket="cloudprivs-test-bucket")

    svc = Service(
        "s3",
        fake_session,
        executor=None,
        regions=["us-east-1"],
        injected_args=injected_args,
    )
    client = svc.clients[0]
    result = svc.test_permission("list_buckets", client)

    assert result.status == OpStatus.SUCCEEDED
    names = [b["Name"] for b in result.results["Buckets"]]
    assert "cloudprivs-test-bucket" in names
