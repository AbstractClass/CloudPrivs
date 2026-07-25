import boto3
import botocore.exceptions
import pytest

from cloudprivs.providers.aws.service import Service
from cloudprivs.providers.aws.service import TestStatus as OpStatus


@pytest.fixture
def cloudfront_kvs_client():
    # No mocking needed: EndpointResolutionError is raised entirely client-side while
    # constructing the request URL, before any network call would happen, regardless
    # of whether the credentials are real.
    session = boto3.Session(
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    svc = Service(
        "cloudfront-keyvaluestore",
        session,
        executor=None,
        regions=["aws-global"],
    )
    return svc, svc.clients[0]


class TestEndpointResolutionErrors:
    def test_missing_kvs_arn_raises_endpoint_resolution_error(self, cloudfront_kvs_client):
        # Sanity check on the premise of the regression below: confirm botocore
        # itself still raises this for the operation we're testing against.
        _, client = cloudfront_kvs_client
        with pytest.raises(botocore.exceptions.EndpointResolutionError):
            client.describe_key_value_store()

    def test_test_permission_reports_errored_not_an_unhandled_exception(
        self, cloudfront_kvs_client
    ):
        svc, client = cloudfront_kvs_client
        result = svc.test_permission("describe_key_value_store", client)
        assert result.status == OpStatus.ERRORED
        assert isinstance(result.error, botocore.exceptions.EndpointResolutionError)

    def test_test_all_operations_does_not_print_unhandled_exception(
        self, cloudfront_kvs_client, capsys
    ):
        svc, client = cloudfront_kvs_client
        results = svc.test_all_operations(client)
        by_name = {r.name: r for r in results}
        assert by_name["describe_key_value_store"].status == OpStatus.ERRORED

        captured = capsys.readouterr()
        assert "Oopsie woopsie" not in captured.out
        assert "Oopsie woopsie" not in captured.err
