from setuptools import setup, find_packages

setup(
    name='cloudprivs',
    author='Connor MacLeod',
    version='1.1.0',
    packages=find_packages(),
    package_data={
        'cloudprivs.providers.aws': ['*.yaml'],
    },
    include_package_data=True,
    install_requires=[
        'Click',
        'boto3',
        'pyyaml',
        'rich',
    ],
    extras_require={
        'test': [
            'pytest',
            'hypothesis',
            'moto[ec2,s3,kms]',
        ],
    },
    entry_points={
        'console_scripts': [
            'cloudprivs = cloudprivs.cli:cli'
        ],
    },
)