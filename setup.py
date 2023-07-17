from setuptools import setup, find_packages

setup(
    name='cloudprivs',
    author='Connor MacLeod',
    version='1.1.0',
    py_modules=find_packages(),
    include_package_data=True,
    install_requires=[
        'Click',
        'boto3',
        'pyyaml',
    ],
    entry_points={
        'console_scripts': [
            'cloudprivs = cloudprivs.cli:cli'
        ],
    },
)