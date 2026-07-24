from hypothesis import given
from hypothesis import strategies as st

from cloudprivs.providers.aws.service import Service

OPERATION_NAME_ALPHABET = "abcdefghijklmnopqrstuvwxyz_"
operation_names = st.text(
    alphabet=OPERATION_NAME_ALPHABET, min_size=1, max_size=30
)


def make_service(monkeypatch, injected_args, dry_run_operations=None):
    """
    Build a Service instance without touching AWS. __init__ never makes a network
    call - session.client() and get_available_regions() are both local - so fake
    credentials and a region filter are enough.
    """
    import boto3

    if dry_run_operations is not None:
        monkeypatch.setattr(
            "cloudprivs.providers.aws.service.DRY_RUN_OPERATIONS",
            dry_run_operations,
        )
    session = boto3.Session(
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    return Service(
        "ec2",
        session,
        executor=None,
        regions=["us-east-1"],
        injected_args=injected_args,
    )


class TestFindInjectedRule:
    def test_no_rules_for_service_returns_none(self):
        svc_rules = {"s3": [{"list_object": {"args": None, "kwargs": {}}}]}
        assert (
            self._find(svc_rules, "ec2", "describe_instances") is None
        )

    def test_exact_match(self):
        svc_rules = {
            "ec2": [{"allocate_address": {"args": None, "kwargs": {"DryRun": True}}}]
        }
        assert self._find(svc_rules, "ec2", "allocate_address") == {
            "args": None,
            "kwargs": {"DryRun": True},
        }

    def test_partial_match(self):
        svc_rules = {"ec2": [{"describe": {"args": None, "kwargs": {"a": 1}}}]}
        assert self._find(svc_rules, "ec2", "describe_instances") == {
            "args": None,
            "kwargs": {"a": 1},
        }

    def test_first_match_wins_over_generic_rule_below_it(self):
        svc_rules = {
            "ec2": [
                {"describe_images": {"args": None, "kwargs": {"specific": True}}},
                {"describe": {"args": None, "kwargs": {"generic": True}}},
            ]
        }
        assert self._find(svc_rules, "ec2", "describe_images") == {
            "args": None,
            "kwargs": {"specific": True},
        }
        assert self._find(svc_rules, "ec2", "describe_instances") == {
            "args": None,
            "kwargs": {"generic": True},
        }

    def test_generic_rule_above_specific_shadows_it(self):
        # Documents existing (surprising but intentional, per the docstring) behavior:
        # ordering matters, and a generic rule placed first wins even over an exact name.
        svc_rules = {
            "ec2": [
                {"describe": {"args": None, "kwargs": {"generic": True}}},
                {"describe_images": {"args": None, "kwargs": {"specific": True}}},
            ]
        }
        assert self._find(svc_rules, "ec2", "describe_images") == {
            "args": None,
            "kwargs": {"generic": True},
        }

    @staticmethod
    def _find(injected_args, service_name, operation):
        class Dummy:
            pass

        d = Dummy()
        d.injected_args = injected_args
        d.service_name = service_name
        return Service._find_injected_rule(d, operation)


class TestDryRunScopeGating:
    """
    Regression coverage for the substring-collision bug: a short zero-arg rule like
    "create_ipam" must not sweep in unrelated operations like "create_ipam_policy"
    that need other args nobody supplied. Scope gating for dry_run_operations must be
    an EXACT match against injected_args rule names, unlike the partial matching used
    for supplying kwargs via _get_custom_args.
    """

    def test_exact_opt_in_is_added_to_scope(self, monkeypatch):
        svc = make_service(
            monkeypatch,
            injected_args={
                "ec2": [{"allocate_address": {"args": None, "kwargs": {"DryRun": True}}}]
            },
            dry_run_operations={
                "ec2": [{"operation": "allocate_address", "other_required_args": []}]
            },
        )
        assert svc.dry_run_operations == {"allocate_address"}
        assert "allocate_address" in svc.operations

    def test_substring_match_does_not_leak_into_dry_run_scope(self, monkeypatch):
        svc = make_service(
            monkeypatch,
            injected_args={
                "ec2": [{"create_ipam": {"args": None, "kwargs": {"DryRun": True}}}]
            },
            dry_run_operations={
                "ec2": [
                    {"operation": "create_ipam", "other_required_args": []},
                    {
                        "operation": "create_ipam_policy",
                        "other_required_args": ["IpamId"],
                    },
                ]
            },
        )
        assert svc.dry_run_operations == {"create_ipam"}
        assert "create_ipam_policy" not in svc.operations

    def test_no_injected_args_means_no_scope_expansion(self, monkeypatch):
        svc = make_service(
            monkeypatch,
            injected_args={},
            dry_run_operations={
                "ec2": [{"operation": "allocate_address", "other_required_args": []}]
            },
        )
        assert svc.dry_run_operations == set()
        assert "allocate_address" not in svc.operations

    def test_operation_not_in_whitelist_is_never_added_even_if_named(self, monkeypatch):
        # This is the Shield scenario: naming an operation in injected_args alone must
        # never be enough to add it to scope if botocore's model doesn't confirm DryRun
        # support for it.
        svc = make_service(
            monkeypatch,
            injected_args={
                "ec2": [{"enable_something": {"args": None, "kwargs": {}}}]
            },
            dry_run_operations={"ec2": []},
        )
        assert svc.dry_run_operations == set()
        assert "enable_something" not in svc.operations

    def test_prefix_matched_operations_are_never_forced_into_dry_run_operations(
        self, monkeypatch
    ):
        # describe_instances is already in scope via the safe prefix filter; even if it
        # also happened to appear in DRY_RUN_OPERATIONS, forcing DryRun=True on it would
        # stop it from returning real data, so it must not be added to dry_run_operations.
        svc = make_service(
            monkeypatch,
            injected_args={
                "ec2": [{"describe_instances": {"args": None, "kwargs": {}}}]
            },
            dry_run_operations={
                "ec2": [{"operation": "describe_instances", "other_required_args": []}]
            },
        )
        assert "describe_instances" not in svc.dry_run_operations
        assert svc.operations.count("describe_instances") == 1


class TestFindInjectedRuleHypothesis:
    @given(operation=operation_names)
    def test_exact_rule_name_always_matches_itself(self, operation):
        class Dummy:
            pass

        d = Dummy()
        d.injected_args = {"ec2": [{operation: {"args": None, "kwargs": {"x": 1}}}]}
        d.service_name = "ec2"
        assert Service._find_injected_rule(d, operation) == {
            "args": None,
            "kwargs": {"x": 1},
        }

    @given(operation=operation_names)
    def test_unrelated_service_never_matches(self, operation):
        class Dummy:
            pass

        d = Dummy()
        d.injected_args = {"s3": [{operation: {"args": None, "kwargs": {}}}]}
        d.service_name = "ec2"
        assert Service._find_injected_rule(d, operation) is None
