import boto3

from cloudprivs.providers.aws.service import Service


class TestAwsGlobalFallback:
    """
    Regression coverage for a real bug: when get_available_regions() returns nothing
    for a service, Service used to construct its client with the literal string
    "aws-global" as region_name. That's not a real AWS region - for services with a
    genuine global endpoint (billing, ce) botocore's endpoint-ruleset resolution
    happens to sort it out anyway, but for services that are actually regional and
    simply missing from botocore's legacy partition data (bedrock-agent,
    bedrock-runtime, chatbot, etc.), it produces a bogus hostname like
    bedrock-agent.aws-global.amazonaws.com that never resolves - every single
    operation for that service then hangs for the full connect_timeout and reports a
    false connection-timeout instead of ever getting a real answer.

    Confirmed live against real AWS credentials: bedrock-agent went from "every
    operation times out" to returning real [+] results once given an actual region
    name instead of the "aws-global" placeholder.
    """

    def test_client_never_constructed_with_literal_aws_global_region(self):
        session = boto3.Session(
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        # bedrock-agent has no entries in get_available_regions() in current
        # botocore, which is exactly the case this bug hit.
        svc = Service("bedrock-agent", session, executor=None)

        assert svc.clients[0].meta.region_name != "aws-global"

    def test_falls_back_to_the_sessions_configured_region(self):
        session = boto3.Session(
            region_name="eu-west-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        svc = Service("bedrock-agent", session, executor=None)

        assert svc.clients[0].meta.region_name == "eu-west-1"

    def test_falls_back_to_us_east_1_when_session_has_no_configured_region(self):
        session = boto3.Session(
            aws_access_key_id="testing", aws_secret_access_key="testing"
        )
        svc = Service("bedrock-agent", session, executor=None)

        assert svc.clients[0].meta.region_name == "us-east-1"

    def test_reporting_label_is_still_aws_global_for_filtering_purposes(self):
        # The internal "aws-global" label must be preserved for self.regions (used by
        # the region filter and the "always keep global services in scope" behavior)
        # even though it's never passed to session.client() directly.
        session = boto3.Session(
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        svc = Service("bedrock-agent", session, executor=None)

        assert svc.regions == ["aws-global"]

    def test_global_service_with_real_regions_is_unaffected(self):
        # sanity check: a normal, properly-regionalized service must still get its
        # real region name, not the fallback logic at all.
        session = boto3.Session(
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        svc = Service("ec2", session, executor=None, regions=["us-east-1"])

        assert svc.clients[0].meta.region_name == "us-east-1"
        assert svc.regions == ["us-east-1"]
