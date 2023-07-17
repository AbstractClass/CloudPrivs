import boto3
import botocore
import click
import logging
import sys
import time
import yaml

from .service import Service, TESTS_LOCATION, MAX_WORKERS, InvalidRegionError
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, TextIO


def scan_service(
    service_name: str, executor: ThreadPoolExecutor, verbose: bool, **kwargs
):
    """
    Helper function for the CLI, run Service.scan(), parse the output and print to terminal.
    Originally made for use in ThreadPoolExecutor.

    :service_name: str - the AWS service name
    :executor: ThreadPoolExecutor - will be passed into Service
    :verbose: bool - Should only successful test be printed
    """
    results = [f"=== {service_name} ==="]
    try:
        client = Service(service_name, executor=executor, **kwargs)
        scan_results = client.scan()
        results += client.pretty_print_scan(scan_results, only_hits=verbose)
    except InvalidRegionError:
        results.append(
            f"[!] Service: {service_name} is not available in the regions supplied"
        )
    # for result in sorted(results, key=lambda x: ["=", "+", "-", "!"].index(x[1])):
    title = ''
    successes = []
    fails = []
    errors = []
    for result in results:
        if result.startswith('='):
            title = result
        elif result.startswith('[+]'):
            successes.append(result)
        elif result.startswith('[-]'):
            fails.append(result)
        else:
            errors.append(result)
    click.echo(click.style(title,fg="white"))
    for i in sorted(successes):
        click.echo(click.style(i, fg="green"))
    for i in sorted(fails):
        click.echo(click.style(i, fg="red"))
    for i in sorted(errors):
        click.echo(click.style(i, fg="red"))

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
    "--parallel/--no-parallel",
    is_flag=True,
    default=True,
    show_default=True,
    help="Should run in parallel, max threads is 15",
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
    parallel: bool,
    verbose: bool,
):
    executor = ThreadPoolExecutor(1)
    if parallel:
        executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    if profile:
        session = boto3.Session(profile_name=profile)
    else:
        session = boto3.Session()
    try:
        click.echo("[*] Established AWS Session")
        session.client("sts").get_caller_identity()
        click.echo("[*] Validated credentials")
    except botocore.exceptions.ClientError as e:
        click.echo(
            click.style(
                "[!] Unable to contact AWS using these creds, are you sure they are valid?",
                fg="red",
            ),
            err=True,
        )
        raise e

    with open(TESTS_LOCATION, "r") as h_tests:
        injected_vars = yaml.safe_load(h_tests)

    if custom_tests:
        extra_tests = yaml.safe_load(custom_tests)
        injected_vars.update(extra_tests)
    click.echo("[*] Loaded test arguments")

    target_services = session.get_available_services()
    if services:
        target_services = [
            s for s in target_services if s in services
        ]  # We don't use reduce in the python world :P

    click.echo("[*] Enumerated services and regions")

    start = time.time()
    futures = [
        executor.submit(
            scan_service(
                service,
                executor,
                not verbose,
                session=session,
                regions=regions,
                injected_args=injected_vars,
            )
        )
        for service in target_services
    ]
    for _ in as_completed(futures):
        pass
    click.echo(f"Finished in {time.time() - start:.2f} seconds")
    click.echo("Happy hunting ;)")
    return
