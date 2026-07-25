import io
from unittest.mock import patch

import boto3
from rich.console import Console
from rich.progress import Progress
from rich.text import Text

from cloudprivs.providers.aws.service import Service


def make_service_with_progress():
    session = boto3.Session(
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    progress = Progress()
    task_id = progress.add_task("ec2", total=10)
    status_text = Text("")
    svc = Service(
        "ec2",
        session,
        executor=None,
        regions=["us-east-1"],
        progress=progress,
        task_id=task_id,
        status_text=status_text,
    )
    return svc, progress, task_id, status_text


class TestProgressReporting:
    def test_report_progress_advances_task_without_changing_description(self):
        svc, progress, task_id, status_text = make_service_with_progress()
        before_description = progress.tasks[0].description

        svc._report_progress("ec2->allocate_address in us-east-1")

        assert progress.tasks[0].description == before_description
        assert progress.tasks[0].completed == 1

    def test_report_progress_updates_status_text_instead(self):
        svc, progress, task_id, status_text = make_service_with_progress()

        svc._report_progress("ec2->allocate_address in us-east-1")
        assert status_text.plain == "ec2->allocate_address in us-east-1"

        svc._report_progress("ec2->describe_instances in us-east-1")
        assert status_text.plain == "ec2->describe_instances in us-east-1"
        # confirm the task description still never changed across multiple calls
        assert progress.tasks[0].description == "ec2"

    def test_no_progress_or_status_text_is_a_no_op(self):
        session = boto3.Session(
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        svc = Service(
            "ec2", session, executor=None, regions=["us-east-1"]
        )
        svc._report_progress("should not raise")  # no progress/task_id supplied


class TestDiagnosticPrinting:
    """
    Regression coverage for routing worker-thread diagnostics (connection timeouts,
    unhandled exceptions) through a supplied rich Console instead of a raw print() -
    a raw print() from a worker thread while a Live display is active writes straight
    to the terminal without coordinating with it, which is what caused the progress
    bars to flicker/get corrupted.
    """

    def _make_service(self, console=None):
        session = boto3.Session(
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        return Service(
            "s3", session, executor=None, regions=["us-east-1"], console=console
        )

    def test_diagnostic_routes_through_console_when_supplied(self):
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False)
        svc = self._make_service(console=console)

        svc._print_diagnostic("[!] something went wrong")

        assert "something went wrong" in buf.getvalue()

    def test_diagnostic_falls_back_to_stderr_without_console(self, capsys):
        svc = self._make_service(console=None)

        svc._print_diagnostic("[!] something went wrong")

        captured = capsys.readouterr()
        assert "something went wrong" in captured.err

    def test_unhandled_exception_in_test_all_operations_uses_console(self):
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False)
        svc = self._make_service(console=console)
        client = svc.clients[0]

        with patch.object(
            svc, "test_permission", side_effect=RuntimeError("boom")
        ):
            svc.test_all_operations(client)

        output = buf.getvalue()
        assert "Oopsie woopsie" in output
        assert "boom" in output
