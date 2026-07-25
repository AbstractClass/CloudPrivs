import os
from typing import Optional, Tuple

import botocore.session
import click
import yaml
from botocore import xform_name

ERROR_SHAPES_LOCATION = os.path.join(os.path.dirname(__file__), "ErrorShapes.yaml")
DRY_RUN_OPERATIONS_LOCATION = os.path.join(
    os.path.dirname(__file__), "DryRunOperations.yaml"
)


def build_error_shapes(session: botocore.session.Session) -> dict:
    """
    Walk every service model available in the given botocore session and collect the
    code/http_status of every shape flagged as an exception. Used to keep
    TEST_FAILED_STRINGS grounded in what AWS actually returns rather than guesswork.
    """
    all_errors = {}
    for service in sorted(session.get_available_services()):
        svc = session.get_service_model(service)
        errors = []
        for shape_name in svc.shape_names:
            shape = svc.shape_for(shape_name)
            if not shape.metadata.get("exception"):
                continue
            error_meta = shape.metadata.get("error", {})
            errors.append(
                {
                    "code": error_meta.get("code", shape_name),
                    "http_status": error_meta.get("httpStatusCode"),
                }
            )
        if errors:
            all_errors[service] = sorted(errors, key=lambda e: e["code"])
    return all_errors


def build_dry_run_operations(session: botocore.session.Session) -> dict:
    """
    Walk every service model available in the given botocore session and collect every
    operation whose input shape accepts a DryRun member, plus whatever other args are
    required to call it. This is the whitelist that gates which mutating operations
    Service is willing to add to scope - see the comment on DRY_RUN_OPERATIONS in
    service.py for why an operation showing up here isn't enough on its own.
    """
    dry_run_ops = {}
    for service in sorted(session.get_available_services()):
        svc = session.get_service_model(service)
        ops = []
        for op_name in svc.operation_names:
            op = svc.operation_model(op_name)
            input_shape = op.input_shape
            if input_shape is None or "DryRun" not in input_shape.members:
                continue
            required = list(input_shape.required_members or [])
            other_required = [m for m in required if m != "DryRun"]
            ops.append(
                {
                    "operation": xform_name(op_name),
                    "other_required_args": other_required,
                }
            )
        if ops:
            dry_run_ops[service] = sorted(ops, key=lambda o: o["operation"])
    return dry_run_ops


def regenerate_metadata_files(
    session: Optional[botocore.session.Session] = None,
) -> Tuple[dict, dict]:
    """
    Build ErrorShapes.yaml's and DryRunOperations.yaml's data from whatever botocore is
    currently installed, and write both files to disk. This is cheap (well under a
    second, no network calls - it's pure local introspection of botocore's bundled
    service models) so Service always calls this itself at import time rather than
    trusting a possibly-stale checked-in copy of the YAML; AWS adds services and
    operations constantly, and there's no flag to opt into freshness.

    The disk write is best-effort only: the returned dicts are what actually drives
    Service's behavior, computed directly from botocore, not read back from disk. If
    the install is read-only (e.g. a system-wide non-editable install) the write is
    skipped rather than crashing the whole tool - the files are a convenience/review
    artifact, not a functional dependency.

    :session: botocore session to introspect; defaults to a fresh one if omitted

    :returns: (error_shapes, dry_run_operations) - the same dicts written to the YAML
        files, for callers (like service.py) that want the data without re-reading it
        from disk.
    """
    session = session or botocore.session.get_session()

    error_shapes = build_error_shapes(session)
    dry_run_ops = build_dry_run_operations(session)

    try:
        with open(ERROR_SHAPES_LOCATION, "w") as f:
            yaml.dump(error_shapes, f, sort_keys=True, default_flow_style=False)
        with open(DRY_RUN_OPERATIONS_LOCATION, "w") as f:
            yaml.dump(dry_run_ops, f, sort_keys=True, default_flow_style=False)
    except OSError:
        pass

    return error_shapes, dry_run_ops


@click.command("generate-aws-metadata")
def generate_aws_metadata():
    """
    Regenerate ErrorShapes.yaml and DryRunOperations.yaml from the AWS service models
    bundled with the currently installed botocore and print a summary. Service already
    does this itself automatically on every run, so this command is just a convenience
    for regenerating the files on their own (e.g. to review a diff) without needing to
    run a scan.
    """
    click.echo("[*] Building ErrorShapes.yaml and DryRunOperations.yaml from service models...")
    error_shapes, dry_run_ops = regenerate_metadata_files()

    click.echo(f"    -> {len(error_shapes)} services with exception shapes")
    total_ops = sum(len(v) for v in dry_run_ops.values())
    click.echo(f"    -> {len(dry_run_ops)} services, {total_ops} operations support DryRun")
    click.echo("[*] Done. Diff the two files and review before committing.")


if __name__ == "__main__":
    generate_aws_metadata()
