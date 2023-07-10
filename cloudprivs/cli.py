from cloudprivs.providers import *
import cloudprivs.providers as providers
import click
import importlib

from typing import Optional, List


@click.group("cli")
def cli():
    """
    CloudPrivs - Resolve Permissions from credentials

    To see arguments for each provider supply the provider name with '--help' (i.e. cloudprivs aws --help)
    """
    click.echo(
        """
    
   ________                ______       _           
  / ____/ /___  __  ______/ / __ \_____(_)   _______
 / /   / / __ \/ / / / __  / /_/ / ___/ / | / / ___/
/ /___/ / /_/ / /_/ / /_/ / ____/ /  / /| |/ (__  ) 
\____/_/\____/\__,_/\__,_/_/   /_/  /_/ |___/____/  
                                                    
    by Connor MacLeod - twitter@0xc130d github@AbstractClass
    """
    )


for provider in providers.__all__:
    provider_cli = importlib.import_module(f"cloudprivs.providers.{provider}.cli")
    cli.add_command(getattr(provider_cli, provider))

if __name__ == "__main__":
    cli()
