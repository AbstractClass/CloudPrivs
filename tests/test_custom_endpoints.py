import boto3

from cloudprivs.providers.aws.service import Service


def make_service(injected_args, service="ec2"):
    session = boto3.Session(
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    return Service(
        service,
        session,
        executor=None,
        regions=["us-east-1"],
        injected_args=injected_args,
    )


class TestCustomEndpoints:
    def test_no_endpoints_key_uses_default_endpoint(self):
        svc = make_service({})
        assert svc.endpoint_url is None
        assert svc.clients[0].meta.endpoint_url == "https://ec2.us-east-1.amazonaws.com"

    def test_endpoint_override_for_matching_service_is_used(self):
        custom = "https://ec2-gamma.us-east-1.amazonaws.com"
        svc = make_service({"_endpoints": {"ec2": custom}})
        assert svc.endpoint_url == custom
        assert svc.clients[0].meta.endpoint_url == custom

    def test_endpoint_override_only_applies_to_the_named_service(self):
        custom = "https://ec2-gamma.us-east-1.amazonaws.com"
        svc = make_service({"_endpoints": {"s3": custom}}, service="ec2")
        assert svc.endpoint_url is None
        assert svc.clients[0].meta.endpoint_url == "https://ec2.us-east-1.amazonaws.com"

    def test_endpoints_key_is_never_confused_for_a_service_operation_rule(self):
        # "_endpoints" must not leak into the per-service operation-matching rules -
        # it's a sibling top-level key, not a service name, and no real operation name
        # could ever contain the literal substring "_endpoints" anyway, but confirm
        # normal rule lookups for the actual service are unaffected by its presence.
        svc = make_service(
            {
                "_endpoints": {"ec2": "https://ec2-gamma.us-east-1.amazonaws.com"},
                "ec2": [{"describe": {"args": None, "kwargs": {"a": 1}}}],
            }
        )
        assert svc._find_injected_rule("describe_instances") == {
            "args": None,
            "kwargs": {"a": 1},
        }
