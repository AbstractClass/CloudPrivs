import boto3
import botocore
import os
import sys
import time

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, List, Tuple, Set

LINE_CLEAR = "\033[K"
MAX_WORKERS = 30
TESTS_LOCATION = os.path.join(os.path.dirname(__file__), "CustomTests.yaml")


class TestStatus(Enum):
    SUCCEEDED = 0
    FAILED = 1
    ERRORED = 2


class InvalidRegionError(Exception):
    """
    Custom error for edge case when user specifies a region filter and one of the services is not available in the regions that match the filter
    """

    def __init__(self, service, region):
        super().__init__(f"Invalid region filter for {service} service: {region}")


@dataclass
class OperationPermissions:
    """
    Simple data structure to contain the results of a single permissions test
    We store things here for each individual test to avoid the overhead to 15 threads trying
    to update the same object at the same time
    """

    name: str
    region: str
    status: TestStatus
    results: Optional[dict]
    error: Optional[Exception]


@dataclass
class OperationPermissionsByRegion:
    """
    Dataclass to store success/failure of a boto3 client operation by region
    Each set will contain the regions that succeeded/failed/errored for the operation
    """

    name: str
    regions_tested: set
    succeeded: List[Optional[OperationPermissions]]
    failed: List[Optional[OperationPermissions]]
    errored: List[Optional[OperationPermissions]]


class Service:
    def __init__(
        self,
        service: str,
        session: boto3.session.Session,
        executor: Optional[ThreadPoolExecutor],
        regions: Optional[List[str]] = None,
        injected_args: Optional[Dict[str, List[dict]]] = None,
        timeout: int = 3,  # TODO make this a CLI arg?
        retries: int = 0,
    ):
        """
        Wrapper around boto3 client to track resources and tests

        :service: boto3 service name
        :session: boto3 session object, will create a bare client if not provided. Useful for credential refreshing
        :regions: list of regions to test, partial matches are accepted (i.e. ['us', 'eu-west'] will test all us and eu-west regions)
        :injected_args: dictionary of arguments to inject into boto3 calls, this is loaded from a YAML file in the provider module
            the format for injected_args is:
            { '$SERVICE_NAME': [
                { 'operation_name': {
                    'args': [$LIST,$OF,$ARGS],
                    'kwargs': { '$KEY': '$VALUE', }
                },
            ]}
        :executor: A ThreadPoolExecutor to do bulk testing in parallel
        :timeout: how long to wait for a connection to AWS
        :retries: how many times to retry the AWS connection before erroring
        """
        self.service_name = service
        self.session = session
        if not executor:
            executor = ThreadPoolExecutor(1)  # Single Threaded with no extra logic ;)
        self.executor = executor
        self.config = botocore.client.Config(
            connect_timeout=timeout, retries={"max_attempts": retries}
        )
        # TODO move these options to a file?
        self._operation_safety_filters = {
            "get_",
            "list_",
            "describe_",
        }  # these calls are safe and won't incur charges (probably)
        if not injected_args:
            self.injected_args = {}
        else:
            self.injected_args = injected_args

        self.regions = self.session.get_available_regions(service)

        # except AttributeError:  # Some services don't have regions
        #    self.regions = ['aws-global']

        if not self.regions:
            self.regions = ["aws-global"]

        if regions:  # apply region filter
            regions = list(regions)
            regions.append("aws-global")  # Always keep global services in scope
            filtered_regions = []
            for (
                region
            ) in (
                self.regions
            ):  # I think this is really ugly but I can't think of any better way to do it
                for region_filter in regions:
                    if region_filter in region:
                        filtered_regions.append(region)
            self.regions = filtered_regions

        if (
            len(self.regions) > 0
        ):  # this could be more DRY but I like how explicit this is
            self.clients = [
                session.client(service, region_name=region, config=self.config)
                for region in self.regions
            ]
        else:
            # self.clients = [session.client(service, config=self.config, region_name="aws-global")]
            raise InvalidRegionError(
                self.service_name, regions
            )  # Edge case where a region filter excludes all regions where the service is available

        self.operations = []
        for op in self.clients[0].meta.method_to_api_mapping.keys():
            for pattern in self._operation_safety_filters:
                if op.startswith(pattern):
                    self.operations.append(op)

        self.method_map = self.clients[
            0
        ].meta.method_to_api_mapping  # sugar, unused unless I decide to start outputting the AWS permission name instead of the boto3 function name

    def test_permission(
        self, operation: str, client: object, *args, **kwargs
    ) -> OperationPermissions:
        """
        Test if the client has permission to run the given operation.
        Args can be injected via args and kwargs, we set these in the YAML to avoid accidentally modifying the infrastructure.
        If we brute forced out the args and just blindly re-ran functions, we could cause damage to the infrastructure (just creating/updating and deleting resources willy nilly)

        :operation: boto3 client function to test
        :client: boto3 client object
        :args: arguments to pass to the boto3 call
        :kwargs: keyword arguments to pass to the boto3 call

        :returns: OperationPermissions object
        """
        region = client.meta.region_name
        status: TestStatus
        results = None
        error = None
        try:
            results = getattr(client, operation)(
                *args, **kwargs
            )  # invoke function from strings
            status = TestStatus.SUCCEEDED
        except (
            botocore.exceptions.ParamValidationError,
            botocore.exceptions.NoAuthTokenError,
        ) as e:  # Function needs arguments, no point in testing it
            status = TestStatus.ERRORED
            error = e
        except botocore.exceptions.ClientError as e:
            client_exceptions = dir(client.exceptions)
            if e.response["Error"]["Code"] != "ClientError":
                status = (
                    TestStatus.SUCCEEDED
                )  # Hit an error that isn't related to auth, this is a success because auth errors always trigger first
                error = e
            else:
                status = TestStatus.FAILED
                error = e
        return OperationPermissions(
            name=operation, region=region, status=status, results=results, error=error
        )

    def _get_custom_args(self, operation) -> Tuple[list, dict]:
        """
        Locate the args and kwargs for a given operation from the serialized YAML structure.
        Because we allow partial matched in the operation name we locate the first match and return it.
        This mean you can make a rule that applies to all 'describe' operations by setting the operation name
        in the YAML to 'describe'. If you want to override this case then you must place the overriding rule
        ABOVE the generic rule. i.e.
        - describe_specific:
            args:
                - special_case
            kwargs:
                special: True
        - describe:
            args:
                - generic
            kwargs:
                special: False
        """
        args = []
        kwargs = {}
        if self.service_name not in self.injected_args:
            return (args, kwargs)

        for rule in self.injected_args[self.service_name]:
            rule_name = list(rule.keys())[0]
            if (
                rule_name in operation
            ):  # partial matches accepted on a "first found" basis
                rule = rule[rule_name]
                if rule["kwargs"]:
                    kwargs.update(rule["kwargs"])
                if rule["args"]:
                    args += rule["args"]
                break
        return (args, kwargs)

    def test_all_operations(self, client) -> List[OperationPermissions]:
        """
        Test all operations for the given client. We also inject args by pattern matching
        the service and operation against the injected_args dictionary. see the _get_custom_args
        docs for more information on the matching system.

        :client: boto3 client object

        :returns: List of OperationPermissions objects for each operation tested
        """
        results = []
        futures = {}

        for operation in self.operations:
            args, kwargs = self._get_custom_args(operation)
            futures[
                self.executor.submit(
                    self.test_permission, operation, client, *args, **kwargs
                )
            ] = (operation, client.meta.region_name)

        for future in as_completed(futures):  # wait 5s per future before timeout
            (operation, region) = futures[future]
            print(
                f"...waiting for {self.service_name}->{operation} in {region}",
                end="\r",
            )
            try:
                results.append(future.result())
            except (
                botocore.exceptions.ConnectTimeoutError,
                botocore.exceptions.EndpointConnectionError,
            ):
                print(
                    f"[!] Connection timeout: {self.service_name}->{operation} in {region}",
                    file=sys.stderr,
                )
            except AttributeError as e:
                raise e
                print(
                    f"[!] Boto3 LIED! {self.service_name}->{operation} isn't in {self.service_name}",
                    file=sys.stderr,
                )
            except Exception as e:
                print(
                    f"[!] Oopsie woopsie :3, hit an unhandled exception at {self.service_name}->{operation} in {region}: {e}",
                    file=sys.stderr,
                )
                print(e)
            finally:
                print(end=LINE_CLEAR)

        return results

    def scan(self) -> Dict[str, OperationPermissionsByRegion]:
        """
        Scan all operations for the service and all regions, then translate the results to be grouped by region for easier analysis.
        The results are translated from the region being the primary key to the method being the primary key, this is done to maximize re-use of clients.

        :returns: Dict with keys of AWS operation names and values of OperationPermissionsByRegion objects
        """
        operation_permissions = []
        operation_permissions_by_region = {}
        futures = {
            self.executor.submit(self.test_all_operations, client): client
            for client in self.clients
        }
                
        for future in as_completed(futures):
            print(f"...awaiting {futures[future].meta.region_name}", end="\r")
            operation_permissions += future.result()
            print(end=LINE_CLEAR)

        # Translate List of OperationPermissions into a list of OperationPermissionsByRegion
        for operation in operation_permissions:
            if operation.name not in operation_permissions_by_region.keys():
                operation_permissions_by_region[
                    operation.name
                ] = OperationPermissionsByRegion(
                    name=operation.name,
                    regions_tested=set(),
                    succeeded=[],
                    failed=[],
                    errored=[],
                )

            operation_permissions_by_region[operation.name].regions_tested.add(
                operation.region
            )

            match operation.status:
                case TestStatus.SUCCEEDED:
                    operation_permissions_by_region[operation.name].succeeded.append(
                        operation
                    )
                case TestStatus.FAILED:
                    operation_permissions_by_region[operation.name].failed.append(
                        operation
                    )
                case TestStatus.ERRORED:
                    operation_permissions_by_region[operation.name].errored.append(
                        operation
                    )

        return operation_permissions_by_region
#### YOU WERE HERE WHEN YOU STOPPED ###
# fix pretty print to fit the new model for OperationPermissionsByRegion
# have option to print error and results under the output line
    def pretty_print_scan(
        self, scan_results: Dict[str, OperationPermissionsByRegion], only_hits=True
    ) -> list:
        """
        Format scan and return str so we can handle the printing elsewhere.

        :scan_results: Dict with keys of AWS operation names and values of OperationPermissionsByRegion objects
        """
        formatted_results = []
        for operation, results in scan_results.items():
            if only_hits:
                results_marker_map = {
                    "+": results.succeeded,
                }
            else:
                results_marker_map = {
                    "+": results.succeeded,
                    "-": results.failed,
                    "!": results.errored,
                }
            for marker, test_results in results_marker_map.items():
                regions = set(test.region for test in test_results)
                if regions:
                    if regions == results.regions_tested:
                        formatted_results.append(f"[{marker}] {operation} - All Regions")
                    else:
                        formatted_results.append(f"[{marker}] {operation} - {','.join(regions)}")

        return formatted_results  # TODO consider just str appending the whole time and abandon the list


if __name__ == "__main__":
    import time

    PROFILE = "default"
    # Get AWS Session
    session = boto3.Session(profile_name=PROFILE)  # TODO click cli
    services = session.get_available_services()
    start = time.time()
    for service in services:
        if service in ["budgets", "iam", "lambda"]:
            print(f"=== {service} ===")
            client = Service(service, session, regions=["us", "eu"])
            results = client.scan(parallel=True)
            client.pretty_print_scan(results)
            print(end="\r", flush=True)
    print(f"Finished in {time.time() - start:.2f} seconds")
