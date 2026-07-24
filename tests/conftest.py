import boto3
import botocore.exceptions
import pytest


def make_client_error(code: str, http_status: int, operation_name: str = "TestOp"):
    response = {
        "Error": {"Code": code, "Message": f"{code} for testing"},
        "ResponseMetadata": {"HTTPStatusCode": http_status},
    }
    return botocore.exceptions.ClientError(response, operation_name)


@pytest.fixture
def fake_session():
    """
    A boto3 session with well-formed but fake credentials. Enough to construct
    clients and call Service() - which never makes a network call in __init__ -
    without touching real AWS.
    """
    return boto3.Session(
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
