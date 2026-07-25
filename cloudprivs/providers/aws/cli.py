import boto3
import botocore
import click
import logging
import sys
import time
import yaml

from .service import Service, TESTS_LOCATION, MAX_WORKERS, InvalidRegionError
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.console import Console, Group
from rich.live import Live
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    MofNCompleteColumn,
    TimeElapsedColumn,
)
from rich.text import Text
from typing import Optional, List, TextIO


def scan_service(
    service_name: str,
    executor: ThreadPoolExecutor,
    verbose: bool,
    console: Console,
    progress: Progress,
    status_text: Text,
    **kwargs,
):
    """
    Helper function for the CLI, run Service.scan(), parse the output and print to terminal.

    :service_name: str - the AWS service name
    :executor: ThreadPoolExecutor - will be passed into Service
    :verbose: bool - Should only successful test be printed
    :console: rich Console to print results through
    :progress: rich Progress driving the per-service task for this scan
    :status_text: rich Text updated with which operation is currently being tested,
        rendered as its own line above the progress bars (see aws() below)
    """
    results = [f"=== {service_name} ==="]
    try:
        client = Service(service_name, executor=executor, **kwargs)
        task_id = progress.add_task(
            f"{service_name}",
            total=len(client.operations) * len(client.clients),
        )
        client.progress = progress
        client.task_id = task_id
        client.status_text = status_text
        client.console = console
        scan_results = client.scan()
        progress.remove_task(task_id)
        results += client.pretty_print_scan(scan_results, only_hits=verbose)
    except InvalidRegionError:
        results.append(
            f"[!] Service: {service_name} is not available in the regions supplied"
        )
    title = ""
    successes = []
    fails = []
    errors = []
    for result in results:
        if result.startswith("="):
            title = result
        elif result.startswith("[+]"):
            successes.append(result)
        elif result.startswith("[-]"):
            fails.append(result)
        else:
            errors.append(result)
    console.print(title, style="white", markup=False)
    for i in sorted(successes):
        console.print(i, style="green", markup=False)
    for i in sorted(fails):
        console.print(i, style="red", markup=False)
    for i in sorted(errors):
        console.print(i, style="red", markup=False)


@click.option(
    "--regions",
    "-r",
    default=[],
    multiple=True,
    help='A list of filters to match against regions, i.e. "us", "eu-west", "ap-north-1"',
)
@click.option(
    "--custom-tests",
    "-t",
    type=click.File("r"),
    help="location of custom tests YAML file. Read docs for more info",
)
@click.option(
    "--profile",
    "-p",
    help="The name of the AWS profile to scan, if not specified ENV vars will be used",
)
@click.option(
    "--services",
    "-s",
    multiple=True,
    help="Only test the given services instead of all available services",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show failed and errored tests in the output",
)
@click.command()
def aws(
    regions: Optional[List[str]],
    custom_tests: Optional[TextIO],
    profile: Optional[str],
    services: Optional[List[str]],
    verbose: bool,
):
    console = Console()
    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    if profile:
        session = boto3.Session(profile_name=profile)
    else:
        session = boto3.Session()
    try:
        console.print("[*] Established AWS Session", style="cyan", markup=False)
        session.client("sts").get_caller_identity()
        console.print("[*] Validated credentials", style="cyan", markup=False)
    except botocore.exceptions.ClientError as e:
        console.print(
            "[!] Unable to contact AWS using these creds, are you sure they are valid?",
            style="red",
            markup=False,
        )
        raise e

    with open(TESTS_LOCATION, "r") as h_tests:
        injected_vars = yaml.safe_load(h_tests)

    if custom_tests:
        extra_tests = yaml.safe_load(custom_tests)
        injected_vars.update(extra_tests)
    console.print("[*] Loaded test arguments", style="cyan", markup=False)

    target_services = session.get_available_services()
    if services:
        target_services = [
            s for s in target_services if s in services
        ]  # We don't use reduce in the python world :P

    console.print("[*] Enumerated services and regions", style="cyan", markup=False)

    start = time.time()
    # status_text is rendered as its own line above the progress bars (see the Group
    # below), separate from any task's description - updating a task's description
    # in place every operation made the bar's row bounce/flicker as the
    # "service->operation in region" text grew and shrank.
    status_text = Text("", style="dim")
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    )
    with Live(Group(status_text, progress), console=console, refresh_per_second=10):
        overall_task = progress.add_task(
            "Scanning services", total=len(target_services)
        )
        for service in target_services:
            scan_service(
                service,
                executor,
                not verbose,
                console,
                progress,
                status_text,
                session=session,
                regions=regions,
                injected_args=injected_vars,
            )
            progress.advance(overall_task)

    console.print(
        f"Finished in {time.time() - start:.2f} seconds", style="white", markup=False
    )
    console.print("Happy hunting ;)", style="white", markup=False)
    return
