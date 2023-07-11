# CloudPrivs
*I got creds, now what?*

## Overview
CloudPrivs is a tool that leverages the existing power of SDKs like Boto3 to brute force privileges of all cloud services to determine what privileges exist for a given set of credentials.

This tool is useful for Pentesters, Red Teamer's and other security professionals. Cloud services typically offer no way to determine what permissions a given set of credentials has, and the shear number of services and operations make it a daunting task to manually confirm. This can mean privilege escalation might be possible from a set of credentials, but one would never know it because they simply don't know the AWS credentials they found can execute Lambda.

## Installation
**Not currently available on PyPi but I'm working on it**
### Pip (simple)
```bash
git clone https://github.com/AbstractClass/CloudPrivs
cd CloudPrivs
python -m pip install -e .
```
This is without a virtual environment and not recommended if you run many python programs

### Pip (reommended)
```bash
git clone https://github.com/AbstractClass/CloudPrivs
cd CloudPrivs
python -m  venv venv/
./venv/bin/activate # activate.ps1 on windows
python -m pip install -e .
```

You can also use Pyenv+virtualenv if available:
```bash
git clone https://github.com/AbstractClass/CloudPrivs
cd CloudPrivs
pyenv virtualenv CloudPrivs
pyenv local CloudPrivs
python -m pip install -e .
```

## Usage
`cloudprivs [PROVIDER] [ARGS]`

### Providers
Currently the only provider available is AWS, but I am working on GCP. If you'd like to help see [#Customizing](#customizing).

### AWS
```
Options:
  -v, --verbose                Show failed and errored tests in the output
  --parallel / --no-parallel   Should run in parallel, max threads is 15
                               [default: parallel]
  -s, --services TEXT          Only test the given services instead of all
                               available services
  -p, --profile TEXT           The name of the AWS profile to scan, if not
                               specified ENV vars will be used
  -t, --custom-tests FILENAME  location of custom tests YAML file. Read docs
                               for more info
  -r, --regions TEXT           A list of filters to match against regions,
                               i.e. "us", "eu-west", "ap-north-1"
  --help                       Show this message and exit.
```

### Tips

Multiple arguments are supported for `--region` and `--service` however they must be supplied with the flag each time, i.e. `cloudprivs aws -r us -r eu -s ec2 -s lambda`. I don't like it either but it is a limitation in Click I have not found a workaround for yet.

>Note that the `region` flag supports partial matches, most common arguments are `-r us -r eu` to only cover the common regions

Results are displayed grouped by region and each line contains a test case and the result of the test.

### Errors
If the tool encounters unexpected errors while testing, they will emit to `stderr` as they occur, which means they can interrupt the flow of output, if you don't want to see them you can redirect stderr.

### How it works
Unlike other tools such as [WeirdAAL](https://github.com/carnal0wnage/weirdAAL) that hand write each test case, CloudPrivs directly queries the Boto3 SDK to dynamically generate a list of all available services and all available regions for each service, 

Once a full list is generated, each function is called without arguments by default, although the option to add custom arguments per operation is supported (more info at [#Customizing](#customizing))

> Note: some AWS functions can incur costs when called, I have deny-listed all operations starting with `open` or `purchase` to mitigate accidental costs, which appears to be safe in my own testing, but please use this with caution. I don't guarantee you won't accidentally incur costs when calling all these functions (even if it's without arguments)

## Customizing
CloudPrivs supports easy extension/customizing in two areas:

- Providers
- Custom tests

### Providers
To implement a new provider (ex. GCP) is simple

1. Write the logic to do the tests, naming convention and structure does not matter
2. Under the `CloudPrivs/providers` folder, create a new folder for your provider (ex. 'gcp')
3. In the `CloudPrivs/providers/__init__.py` file, add your provider to the `__all__` variable, it must match the name of the folder
4. Create a file called `cli.py` in your provider folder
5. Use the [Click](https://click.palletsprojects.com/en/8.1.x/) to create a CLI for your provider and name your cli entry function `cli` (see the AWS provider for reference)
6. Done! Running `cloudprivs <provider>` should now show your CLI

### Custom Tests
The AWS provider supports the injection of arguments when calling AWS functions. This feature is provided because often times an AWS function requires arguments to be called and in some cases these arguments can be fixed variables. This means if we can provided dummy variables we can increase our testing coverage. In other cases we can inject arguments like `dryrun=true` to make calls go faster.

Custom tests are stored in a YAML file at `cloudprivs/providers/aws/CustomTests.yaml`. 

The structure of the YAML is as follows:
```yaml
---
<service-name>:
    - <function-name>:
        args:
            - <arg1>
            - <arg2>
        kwargs:
            arg1: val1
            arg2: val2
```

>Note: The function name works with partial matches, this means you can use a function name like 'describe_' and the arguments specified will be injected into all functions that contain 'describe_'. Rules are matched on a 'first found' basis, so if you'd like to override a generic rule, place your more specific rule **above** the generic rule. ex:
```yaml
ec2:
    - describe_instances
        args:
        kwargs:
            DryRun: True
            NoPaginate: True
    - describe_
        args:
        kwargs:
            DryRun: True
```

### Adding New Rules
New rules can be added by either modifying the existing `CustomTests.yaml` file or creating a new YAML file and specifying it with the `--custom-tests` flag. The new file will be merged with the existing tests file and any duplicate values will be overridden with the supplied file getting priority.

## Library Usage
### AWS
The AWS provider is written as a Library for integration into other tools. You can use it as follows:
```python
import boto3
from cloudprivs.providers.aws import service
from concurrent.futures import ThreadPoolExecutor

session = boto3.Session('default')
with ThreadPoolExecutor(15) as executor:
  iam = service.Service('iam', session, executor=executor)
  scan_results = iam.scan() # will cover all regions listed in the executor (all available regions by default)
  formatted_results iam.pretty_print_scan(scan_results)
  print(formatted_results)
```
Everything is fully documented in the code, should be pretty easy to parse.

## Road Map
This tools is functional, but far from complete. I am actively working on new features and am open to contributions, so please feel free to open Issues/Feature Requests, and send PRs.

**Features Planned**
- Add tool to PyPi
- GCP Support
- More custom tests
- JSON output
- Add unit tests
- Refactor return types for scanning functions
- Better error handling, especially for Keyboard Interrupt
- Migration to Golang

