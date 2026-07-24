import string

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from cloudprivs.providers.aws.generate_metadata import ERROR_SHAPES_LOCATION
from cloudprivs.providers.aws.service import TEST_FAILED_STRINGS, Service
from cloudprivs.providers.aws.service import TestStatus as OpStatus

from conftest import make_client_error

with open(ERROR_SHAPES_LOCATION) as _h:
    ALL_ERROR_SHAPES = yaml.safe_load(_h)

# Flatten every (code, http_status) pair observed across every AWS service model, for
# use as a real-world regression fixture rather than just synthetic hypothesis input.
ALL_OBSERVED_ERRORS = [
    (svc, err["code"], err["http_status"])
    for svc, errors in ALL_ERROR_SHAPES.items()
    for err in errors
]

code_strategy = st.text(alphabet=string.ascii_letters, min_size=1, max_size=40)
status_strategy = st.one_of(st.none(), st.integers(min_value=100, max_value=599))


class TestPinnedCases:
    """Specific, known-important cases pinned as regression tests."""

    @pytest.mark.parametrize(
        "code,status",
        [
            ("AccessDenied", 403),
            ("AccessDeniedException", 403),
            ("UnauthorizedOperation", 400),  # classic EC2 shape, no 403
            ("UnauthorizedOperation", None),
            ("NotAuthorizedException", 401),
            ("AuthorizationError", 403),
            ("InsufficientPrivilegesException", 403),
            ("MissingAuthenticationToken", 403),
            ("Forbidden", 403),
            ("SomeWeirdCode", 403),  # nonstandard code, still 403 -> FAILED
        ],
    )
    def test_classified_as_failed(self, code, status):
        error = make_client_error(code, status)
        assert Service.classify_client_error(error) == OpStatus.FAILED

    @pytest.mark.parametrize(
        "code,status",
        [
            ("DryRunOperation", 412),  # the whole dry-run mechanism depends on this
            ("ResourceNotFoundException", 404),
            ("ThrottlingException", 429),
            ("ValidationException", 400),
            ("BucketAlreadyExists", 409),
        ],
    )
    def test_classified_as_succeeded(self, code, status):
        error = make_client_error(code, status)
        assert Service.classify_client_error(error) == OpStatus.SUCCEEDED

    def test_known_false_positive_opt_in_required(self):
        """
        Documents a known accuracy tradeoff: AWS returns OptInRequiredException with
        HTTP 403 for a couple of services (see compute-optimizer in ErrorShapes.yaml),
        even though it means "you have permission but haven't opted into the service",
        not "access denied". The 403 shortcut can't tell these apart from a real
        UnauthorizedOperation-style 403, so this currently (and knowingly) misclassifies
        as FAILED. If that turns out to matter in practice, the fix is to special-case
        specific codes ahead of the 403 check - not to drop the 403 check itself, since
        it catches far more than it misses.
        """
        error = make_client_error("OptInRequiredException", 403)
        assert Service.classify_client_error(error) == OpStatus.FAILED


class TestRealWorldErrorShapes:
    """Regression coverage against every error shape scraped from botocore itself."""

    @pytest.mark.parametrize("service,code,status", ALL_OBSERVED_ERRORS)
    def test_classification_is_deterministic_and_documented(self, service, code, status):
        error = make_client_error(code, status)
        result = Service.classify_client_error(error)
        assert result in (OpStatus.FAILED, OpStatus.SUCCEEDED)
        # 403 must always fail closed (treated as a permissions issue), full stop.
        if status == 403:
            assert result == OpStatus.FAILED


class TestHypothesisProperties:
    @given(status=st.integers(min_value=100, max_value=599).filter(lambda s: s != 403))
    def test_any_failed_string_forces_failed_regardless_of_status(self, status):
        for needle in TEST_FAILED_STRINGS:
            error = make_client_error(f"Some{needle}Exception", status)
            assert Service.classify_client_error(error) == OpStatus.FAILED

    @given(status=st.integers(min_value=100, max_value=599))
    def test_403_always_fails_regardless_of_code(self, status):
        error = make_client_error("TotallyUnrelatedCode", 403)
        assert Service.classify_client_error(error) == OpStatus.FAILED

    @given(code=code_strategy, status=status_strategy)
    def test_never_raises_and_always_returns_a_status(self, code, status):
        error = make_client_error(code, status)
        assert Service.classify_client_error(error) in (
            OpStatus.FAILED,
            OpStatus.SUCCEEDED,
        )

    @given(code=code_strategy)
    def test_non_403_without_failed_substrings_succeeds(self, code):
        if any(needle in code for needle in TEST_FAILED_STRINGS):
            return
        error = make_client_error(code, 500)
        assert Service.classify_client_error(error) == OpStatus.SUCCEEDED
