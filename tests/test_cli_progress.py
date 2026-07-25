import warnings
from unittest.mock import patch

from click.testing import CliRunner
from moto import mock_aws
from rich.console import Console
from rich.progress import Progress

import cloudprivs.providers.aws.cli as aws_cli
import cloudprivs.providers.aws.service as service_module


def _fake_env():
    return {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_DEFAULT_REGION": "us-east-1",
    }


class TestProgressTaskReuse:
    """
    Regression coverage for the progress-bar flicker: the per-service task must be
    created once and reused via Progress.reset() across every service, not
    added/removed per service - add_task()/remove_task() per service made that row
    disappear and reappear for every single service, which is what caused the visible
    flicker.
    """

    def test_per_service_task_is_never_removed_across_multiple_services(self):
        runner = CliRunner()
        remove_task_calls = []
        original_remove = Progress.remove_task

        def counting_remove(self, *a, **kw):
            remove_task_calls.append((a, kw))
            return original_remove(self, *a, **kw)

        with mock_aws(), patch.object(Progress, "remove_task", counting_remove):
            result = runner.invoke(
                aws_cli.aws,
                ["--services", "iam", "--services", "s3"],
                env=_fake_env(),
            )

        assert result.exit_code == 0, result.output
        assert remove_task_calls == []

    def test_only_two_tasks_added_regardless_of_service_count(self):
        runner = CliRunner()
        add_task_calls = []
        original_add = Progress.add_task

        def counting_add(self, *a, **kw):
            add_task_calls.append((a, kw))
            return original_add(self, *a, **kw)

        with mock_aws(), patch.object(Progress, "add_task", counting_add):
            result = runner.invoke(
                aws_cli.aws,
                ["--services", "iam", "--services", "s3", "--services", "kms"],
                env=_fake_env(),
            )

        assert result.exit_code == 0, result.output
        # the overall "Scanning services" task + the single reused per-service task -
        # this must stay at 2 no matter how many services are scanned
        assert len(add_task_calls) == 2


class TestServiceHeaderOrdering:
    """
    Regression coverage for a real (mis)reading bug: test_all_operations prints
    diagnostics (connection timeouts, unhandled exceptions) live from worker threads
    as each operation completes, DURING that service's scan - but the "=== {service}
    ===" header used to only print afterwards, batched together with the results. So
    a slow service's own diagnostics would print before its header had appeared at
    all, landing directly underneath the *previous* service's header instead and
    looking like they belonged to it (observed in the wild: devops-agent's connection
    timeouts appeared to belong to devicefarm, the alphabetically preceding service,
    which itself produces no output since it's just skipped for being unavailable in
    the region filter). The header must always print before anything else the service
    produces.
    """

    def test_header_prints_before_that_services_own_diagnostics(self):
        runner = CliRunner()
        print_calls = []
        original_print = Console.print

        def recording_print(self, *args, **kwargs):
            print_calls.append(args[0] if args else "")
            return original_print(self, *args, **kwargs)

        def boom(*a, **kw):
            raise RuntimeError("simulated failure")

        with mock_aws(), patch.object(Console, "print", recording_print), patch.object(
            service_module.Service, "test_permission", side_effect=boom
        ):
            result = runner.invoke(
                aws_cli.aws, ["--services", "iam"], env=_fake_env()
            )

        assert result.exit_code == 0, result.output
        header_index = next(
            i
            for i, call in enumerate(print_calls)
            if "=== iam ===" in str(call)
        )
        diagnostic_index = next(
            i
            for i, call in enumerate(print_calls)
            if "Oopsie woopsie" in str(call)
        )
        assert header_index < diagnostic_index

    def test_service_with_no_output_at_all_does_not_print_empty_block(self):
        # e.g. a service excluded entirely by the region filter: header prints, then
        # nothing else - no trailing blank Text block.
        runner = CliRunner()
        with mock_aws():
            result = runner.invoke(
                aws_cli.aws,
                ["--services", "devicefarm", "-r", "us-east-2"],
                env=_fake_env(),
            )

        assert result.exit_code == 0, result.output
        assert "=== devicefarm ===" in result.output
        assert "is not available in the regions supplied" in result.output


class TestBannerSyntaxWarning:
    """
    Regression coverage: the ASCII-art banner in cloudprivs/cli.py previously wasn't a
    raw string, so backslash sequences like \\_ and \\( were parsed as (invalid)
    escapes, producing a SyntaxWarning the first time the module was compiled after
    install (silent afterwards once Python's bytecode cache kicked in, which made it
    look like a one-off flake). Compiling the raw source directly - rather than
    import/reload, which can silently reuse a cached .pyc and never re-trigger a
    compile-time warning regardless of whether the source is actually fixed - is what
    makes this deterministic.
    """

    def test_compiling_cli_module_source_emits_no_syntax_warning(self):
        import cloudprivs.cli

        with open(cloudprivs.cli.__file__) as f:
            source = f.read()

        with warnings.catch_warnings():
            warnings.simplefilter("error", SyntaxWarning)
            compile(source, cloudprivs.cli.__file__, "exec")
