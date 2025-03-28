import os
import subprocess
import sys
from enum import Enum
from pathlib import Path
import json
from databricks.sdk import WorkspaceClient
from pyspark.sql import SparkSession


def get_dbutils(spark):
    try:
        if "DATABRICKS_RUNTIME_VERSION" in os.environ:
            from pyspark.dbutils import DBUtils  # noqa

            return DBUtils(spark)
        else:
            return WorkspaceClient().dbutils
    except NameError:
        return WorkspaceClient().dbutils


def get_spark(serverless: bool = False, profile: str = "DEFAULT"):
    """
    Returns a SparkSession or DatabricksSession object based on the environment.

    Parameters:
        serverless (bool, optional): Flag indicating whether to use serverless mode. Defaults to False.
        profile (str, optional): The profile to use for the session. Defaults to "DEFAULT".

    Returns:
        SparkSession or DatabricksSession: The Spark session object based on the environment.
    """
    if "DATABRICKS_RUNTIME_VERSION" in os.environ:
        return SparkSession.builder.getOrCreate()

    from databricks.connect import DatabricksSession

    if serverless:
        return DatabricksSession.builder.serverless().profile(profile).getOrCreate()
    return DatabricksSession.builder.profile(profile).getOrCreate()


def install_package_in_databricks(
    install_aws_cli: bool = False, setup_codeartifact: bool = False
) -> None:
    """
    Install a package in Databricks when developing in a Notebook.

    Args:
        install_aws_cli (bool, optional): Whether to install AWS CLI. Defaults to False.
        setup_codeartifact (bool, optional): Whether to setup CodeArtifact. Defaults to False.
    """

    if "DATABRICKS_RUNTIME_VERSION" not in os.environ:
        print("Detected non-Databricks environment. Skipping package installation.")
        return
    else:
        spark = get_spark()
        dbutils = get_dbutils(spark)
        # dbutils is automatically available in Databricks notebooks
        context = json.loads(
            dbutils.notebook.entry_point.getDbutils().notebook().getContext().toJson()
        )

        if context.get("currentRunId"):
            print("Detected Databricks job run. Skipping package installation.")
            return

    def run_command(command, shell=False) -> None:
        """Run a command using subprocess and handle errors."""
        try:
            subprocess.check_call(command, shell=shell)
        except subprocess.CalledProcessError as e:
            print(f"Error occurred: {e}")
            sys.exit(1)

    if install_aws_cli:
        # Get the architecture of the system
        arch_command = "uname -m"
        arch = subprocess.check_output(arch_command, shell=True).decode().strip()

        # Check the architecture and download the appropriate AWS CLI
        if arch == "x86_64":
            print(
                "Architecture is x86_64. Downloading and installing AWS CLI for x86_64..."
            )
            run_command(
                [
                    "curl",
                    "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip",
                    "-o",
                    "awscliv2.zip",
                ]
            )
        elif arch == "aarch64":
            print(
                "Architecture is aarch64. Downloading and installing AWS CLI for aarch64..."
            )
            run_command(
                [
                    "curl",
                    "https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip",
                    "-o",
                    "awscliv2.zip",
                ]
            )
        else:
            print(f"Unsupported architecture: {arch}")
            sys.exit(1)

        # Create a tmpfs directory for faster extraction
        tmp_dir = "/dev/shm/aws_cli_tmp"  # tmpfs path
        os.makedirs(tmp_dir, exist_ok=True)
        run_command(["unzip", "-o", "awscliv2.zip", "-d", tmp_dir])

        # Install or update the AWS CLI with minimal options
        run_command(
            [
                "sudo",
                os.path.join(tmp_dir, "aws/install"),
                "--install-dir",
                "/usr/local/aws-cli",
                "--bin-dir",
                "/usr/local/bin",
                "--update",
            ]
        )

        # Clean up the temporary directory
        run_command(["rm", "-rf", tmp_dir])

    if setup_codeartifact:
        # Get the CodeArtifact authorization token
        get_token_command = [
            "aws",
            "codeartifact",
            "get-authorization-token",
            "--domain",
            "auctionedge",
            "--domain-owner",
            "364829957068",
            "--region",
            "us-west-2",
            "--query",
            "authorizationToken",
            "--output",
            "text",
        ]
        auth_token = subprocess.check_output(get_token_command).decode().strip()

        # Set pip configuration for the global index URL and extra index URL
        pip_config_commands = [
            [
                "pip",
                "config",
                "--global",
                "set",
                "global.index-url",
                "https://pypi.org/simple",
            ],
            [
                "pip",
                "config",
                "--global",
                "set",
                "global.extra-index-url",
                f"https://aws:{auth_token}@auctionedge-364829957068.d.codeartifact.us-west-2.amazonaws.com/pypi/edge-event-catalog/simple/",
            ],
        ]

        for command in pip_config_commands:
            run_command(command)

    subprocess.run(["pip", "cache", "purge"], check=True)  # noqa S607

    package_dir = str(Path(os.getcwd()).parent)

    subprocess.run(
        [  # noqa S607
            "pip",
            "install",
            "-e",
            f"{package_dir}",
        ],
        check=True,
    )

    src_path = str(Path(os.getcwd()).parent / "src")
    sys.path.append(src_path)
