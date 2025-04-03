from __future__ import annotations

import glob
import json
import os
import re
import subprocess
from enum import Enum
from pathlib import Path
from typing import Any
import yaml
from databricks.sdk import WorkspaceClient
from loguru import logger

FILE_PATH = Path(__file__).resolve()


class EnvironmentEnum(str, Enum):
    development = "dev"
    staging = "staging"
    production = "prod"


class RunContextEnum(str, Enum):
    workspace = "workspace"
    local = "local"
    job = "job"


def get_databricks_run_context(spark) -> RunContextEnum:
    """
    Returns the run context based on the environment.
    """
    if "DATABRICKS_RUNTIME_VERSION" in os.environ:
        dbutils = get_dbutils(spark)
        context = json.loads(
            dbutils.notebook.entry_point.getDbutils().notebook().getContext().toJson()
        )
        if context.get("currentRunId"):
            return RunContextEnum.job
        return RunContextEnum.workspace
    else:
        return RunContextEnum.local


def get_dbutils(spark):
    try:
        if "DATABRICKS_RUNTIME_VERSION" in os.environ:
            from pyspark.dbutils import DBUtils  # noqa

            return DBUtils(spark)
        else:
            return WorkspaceClient().dbutils
    except NameError:
        return WorkspaceClient().dbutils


def find_databricks_yml(start_path: Path = None) -> Path:
    """Search for databricks.yml configuration file in current and parent directories.

    This function searches for a 'databricks.yml' file starting from a given path
    and traversing up through its parent directories until the file is found.

    Args:
        start_path (Path, optional): Starting directory path to begin the search.
            Defaults to current working directory if None.

    Returns:
        Path: Path object pointing to the first found databricks.yml file.

    Raises:
        FileNotFoundError: If no databricks.yml file is found in the directory tree.

    Example:
        >>> config_path = find_databricks_yml()
        >>> print(config_path)
        /path/to/databricks.yml
    """
    if start_path is None:
        start_path = Path.cwd()
    for parent in [start_path] + list(start_path.parents):
        candidate = parent / "databricks.yml"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("databricks.yml not found in any parent directory.")


class BetterBundleConfig:
    """A class for managing and processing Databricks bundle configurations with enhanced functionality.
    The BetterBundleConfig class provides a robust way to handle Databricks bundle configurations,
    offering features such as deep merging of configurations, variable resolution, target-specific
    configurations, and integration with both local and Databricks workspace environments.
    Key Features:
        - Load and merge multiple YAML configuration files
        - Process and apply target-specific configurations
        - Resolve variable references in configuration values
        - Integrate with Databricks workspace context
        - Handle Git information in both local and workspace environments
        - Support for configuration validation using Databricks CLI
    Attributes:
        spark: The SparkSession object used for configuration context
        __dict__: Dynamic dictionary storing all configuration attributes
        >>> # Building config from bundle CLI
        >>> config = BetterBundleConfig.build_with_bundle_cli(spark, target="dev")
        >>> # Building config from YAML file
        >>> config = BetterBundleConfig.build_with_bundle_yml(
        ...     spark,
        ...     target="prod",
        ...     bundle_path="path/to/databricks.yml"
        ... )
        >>> # Accessing configuration values
        >>> value = config.get("some_key", default="default_value")
        >>> widget_value = config.get_with_widgets("widget_key", default="default_value")
    Notes:
        - The class supports both local development and Databricks workspace environments
        - Configuration values can include variable references using ${var.name} syntax
        - Target-specific configurations can override base configurations
        - Git information is automatically included when available
        - Current user information is automatically included in workspace environments
    See Also:
        - Databricks CLI documentation for bundle configuration
        - YAML specification for configuration file format
    """

    def __init__(self, spark, **kwargs):
        self.spark = spark
        self.__dict__ = kwargs

    @classmethod
    def _deep_merge_dicts(cls, d1: dict, d2: dict) -> dict:
        """
        Recursively merge two dictionaries.

        This function performs a deep merge of two dictionaries, where nested dictionaries
        are merged recursively rather than being overwritten.

        Args:
            d1 (dict): The first dictionary to merge (base dictionary)
            d2 (dict): The second dictionary to merge (dictionary to merge into d1)

        Returns:
            dict: A new dictionary containing the merged results of d1 and d2

        Note:
            - If a key exists in both dictionaries and the values are dictionaries,
            they will be merged recursively
            - If a key exists in d2 but not in d1, the value from d2 will be used
            - If a key exists in both dictionaries and the values are not dictionaries,
            the value from d1 will be preserved

        Example:
            >>> dict1 = {'a': 1, 'b': {'x': 2, 'y': 3}}
            >>> dict2 = {'c': 4, 'b': {'y': 5, 'z': 6}}
            >>> deep_merge_dicts(dict1, dict2)
            {'a': 1, 'b': {'x': 2, 'y': 3, 'z': 6}, 'c': 4}
        """
        result = d1.copy()
        for key, value in d2.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = cls._deep_merge_dicts(result[key], value)
            elif key not in result:
                result[key] = value
        return result

    @classmethod
    def _merge_bundles(cls, bundles: list[dict]) -> dict:
        """Merge multiple bundles into a single bundle.

        Takes a list of bundle dictionaries and merges them recursively, with later bundles
        overriding values from earlier ones in case of conflicts.

        Args:
            bundles (list[dict]): List of bundle dictionaries to merge.

        Returns:
            dict: A single merged bundle containing all settings from input bundles.

        Example:
            >>> bundle1 = {"setting1": "value1"}
            >>> bundle2 = {"setting2": "value2"}
            >>> merge_bundles([bundle1, bundle2])
            {'setting1': 'value1', 'setting2': 'value2'}
        """
        merged = {}
        for bundle in bundles:
            merged = cls._deep_merge_dicts(merged, bundle)
        return merged

    @classmethod
    def _load_bundle(cls, bundle_file: Any) -> dict:
        """Load and merge YAML configuration bundles.

        This function reads a base YAML configuration file and any included configuration files,
        then merges them into a single configuration dictionary. Included files are specified
        using the 'include' key in the base configuration, which accepts glob patterns relative
        to the base file's directory.

        Args:
            bundle_file (Union[str, Path]): Path to the base YAML configuration file.

        Returns:
            dict: The merged configuration dictionary containing all settings from the base
                file and included files.

        Raises:
            yaml.YAMLError: If there are syntax errors in any of the YAML files.
            FileNotFoundError: If the base configuration file or any included files cannot be found.

        Example:
            >>> config = load_bundle('config.yml')
            >>> # Where config.yml contains:
            >>> # include:
            >>> #   - 'configs/*.yml'
        """
        bundle_file = Path(bundle_file)
        bundles = []
        try:
            with open(bundle_file) as f:
                base_bundle = yaml.safe_load(f)
                bundles.append(base_bundle)
        except yaml.YAMLError as e:
            logger.exception(f"Error loading base config {bundle_file}: {e}")
            raise
        except FileNotFoundError as e:
            logger.exception(f"Base config file not found: {bundle_file}: {e}")
            raise

        includes = base_bundle.get("include", [])

        if not includes:
            return base_bundle

        for pattern in includes:
            full_pattern = str(bundle_file.parent / pattern)
            for file_path in glob.glob(full_pattern):
                try:
                    with open(file_path) as f:
                        config_part = yaml.safe_load(f)
                        bundles.append(config_part)
                except yaml.YAMLError as e:
                    logger.exception(f"Error loading included config {file_path}: {e}")
                    raise
                except FileNotFoundError as e:
                    logger.exception(
                        f"Included config file not found: {file_path}: {e}"
                    )
                    raise

        final_bundle = cls._merge_bundles(bundles)
        return final_bundle

    @classmethod
    def _override_with_target(cls, root: dict, override: dict) -> dict:
        """
        Recursively overrides values in a target dictionary with values from an override dictionary.

        This function performs a deep merge of two dictionaries, where values from the override
        dictionary take precedence over values in the root dictionary. If both dictionaries
        contain nested dictionaries under the same key, those dictionaries are merged recursively.

        Args:
            root (dict): The base dictionary whose values may be overridden.
            override (dict): The dictionary containing values that will override the root dictionary.

        Returns:
            dict: A new dictionary containing the merged result, where values from override
                take precedence over values from root.

        Examples:
            >>> root = {'a': 1, 'b': {'x': 10, 'y': 20}}
            >>> override = {'b': {'x': 30}, 'c': 3}
            >>> override_with_target(root, override)
            {'a': 1, 'b': {'x': 30, 'y': 20}, 'c': 3}
        """
        result = root.copy()
        for key, value in override.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = cls._override_with_target(result[key], value)
            else:
                result[key] = value
        return result

    @classmethod
    def _apply_target_bundle(cls, bundle: dict, target: str) -> dict:
        """
        Applies target-specific configuration to a bundle configuration dictionary.

        This function takes a bundle configuration dictionary and a target name, merges the
        target-specific configuration with the base configuration, and returns the updated bundle.
        The target-specific configuration is taken from the 'targets' section of the bundle and
        overrides corresponding values in the base configuration.

        Args:
            bundle (dict): The original bundle configuration dictionary.
            target (str): The name of the target whose configuration should be applied.

        Returns:
            dict: A new dictionary containing the merged configuration with target-specific
                settings applied and 'targets' section removed. If the bundle contains a 'bundle'
                key, its 'target' field will be set to the specified target.

        Example:
            base_config = {
                "bundle": {"name": "app"},
                "targets": {
                    "prod": {"bundle": {"env": "production"}}
                }
            }
            result = apply_target_bundle(base_config, "prod")
            # Result: {"bundle": {"name": "app", "env": "production", "target": "prod"}}
        """
        target_bundle = bundle.get("targets", {}).get(target, {})
        updated_bundle = cls._override_with_target(bundle, target_bundle)
        updated_bundle.pop("targets", None)

        if "bundle" in updated_bundle:
            updated_bundle["bundle"]["target"] = target
            # legacy support for environment
            # https://docs.databricks.com/aws/en/dev-tools/bundles/variables
            updated_bundle["bundle"]["environment"] = target
        return updated_bundle

    @staticmethod
    def _process_variables(bundle: dict) -> dict:
        """Process and simplify variables in a bundle configuration.

        This function transforms complex variable definitions in a bundle configuration into their
        effective values. For each variable, it applies the following rules:
        - If the variable has a 'value' key, use that as the effective value
        - If no 'value' but has 'default', use the default as the effective value
        - Otherwise, create a dictionary excluding 'description' and 'type' keys

        Args:
            bundle (dict): A bundle configuration dictionary that may contain a 'variables' key

        Returns:
            dict: The modified bundle with simplified variable values

        Example:
            >>> bundle = {
            ...     'variables': {
            ...         'var1': {'value': 123},
            ...         'var2': {'default': 'abc', 'description': 'test'},
            ...         'var3': {'key1': 1, 'key2': 2, 'type': 'int'}
            ...     }
            ... }
            >>> process_variables(bundle)
            {
                'variables': {
                    'var1': 123,
                    'var2': 'abc',
                    'var3': {'key1': 1, 'key2': 2}
        """
        if "variables" in bundle:
            for var, val in bundle["variables"].items():
                if isinstance(val, dict):
                    if "value" in val:
                        effective_value = val["value"]
                    elif "default" in val:
                        effective_value = val["default"]
                    else:
                        effective_value = {
                            k: v
                            for k, v in val.items()
                            if k not in {"description", "type"}
                        }
                    bundle["variables"][var] = effective_value
        return bundle

    @staticmethod
    def _get_value_by_path(context: dict, parts: list[str]) -> Any:
        """
        Gets a nested value from a dictionary using a path specified as a list of keys.

        This function traverses a nested dictionary structure using the provided path.
        If at any point the path is invalid (key doesn't exist or current value is not a dict),
        it returns None.

        Args:
            context (dict): The dictionary to traverse
            parts (list[str]): List of keys representing the path to the desired value

        Returns:
            Any: The value found at the specified path, or None if path is invalid

        Examples:
            >>> d = {'a': {'b': {'c': 1}}}
            >>> get_value_by_path(d, ['a', 'b', 'c'])
            1
            >>> get_value_by_path(d, ['a', 'x'])
            None
        """
        current = context
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    @classmethod
    def _resolve_string(cls, template_string: str, context: dict) -> str:
        """Resolves variable references in a template string using a given context.

        This method handles two types of string interpolation:
        1. Simple case: When the entire string is a single variable reference
        2. Complex case: When the string contains one or more variable references mixed with literal text

        If the entire string is a single variable template (e.g. "${var.x.y}"),
        the method will return the replacement preserving its datatype.
        If there is additional text around the token, the replacement is converted
        to a string for concatenation.

        Variable references can be:
        - Regular references: using dot notation (e.g., ${path.to.value})
        - Variable references: prefixed with 'var.' (e.g., ${var.path.to.value})

        Args:
            template_string (str): The string containing variable references to be resolved
            context (dict): The context dictionary containing the values for variable resolution
            raise_on_missing_token (bool, optional): If True, throw an exception when a token cannot be resolved.
                                             Defaults to False.
        Returns:
            str: The resolved string with all variable references replaced with their values
                For simple cases with a single token, preserves the original datatype
                For complex cases, always returns a string

        Example:
            >>> context = {'variables': {'x': 'value'}, 'a': {'b': 'result'}}
            >>> _resolve_string("${var.x}", context)
            'value'
            >>> _resolve_string("prefix_${a.b}_suffix", context)
            'prefix_result_suffix'

        Note:
            - If a referenced variable is not found, a warning is logged and the reference remains unresolved
            - The method performs iterative replacement until no more changes can be made
        """
        pattern = re.compile(r"\$\{([^}]+)\}")
        # Check simple case: entire string is a single token.
        tokens = pattern.findall(template_string)
        if len(tokens) == 1 and template_string.strip() == "${" + tokens[0] + "}":
            token = tokens[0]
            if token.startswith("var."):
                keys = token[4:].split(".")
                replacement = cls._get_value_by_path(context.get("variables", {}), keys)
            else:
                parts = token.split(".")
                replacement = cls._get_value_by_path(context, parts)
            if replacement is not None:
                return replacement  # Preserves original datatype

        # Iterative replacement.
        previous = None
        while previous != template_string:
            previous = template_string
            for token in pattern.findall(template_string):
                replacement = None
                if token.startswith("var."):
                    keys = token[4:].split(".")
                    replacement = cls._get_value_by_path(
                        context.get("variables", {}), keys
                    )
                else:
                    parts = token.split(".")
                    replacement = cls._get_value_by_path(context, parts)
                if replacement is not None:
                    template_string = template_string.replace(
                        "${" + token + "}", str(replacement)
                    )
        # No warnings or raising here.
        return template_string

    @classmethod
    def _resolve_bundle(cls, bundle: Any, context: dict) -> Any:
        """Recursively resolves values in a bundle by replacing template strings with context values.

        This function traverses through nested dictionaries and lists, resolving any template strings
        using the provided context. Non-string values are returned unchanged.

        Args:
            bundle (Any): The bundle to resolve. Can be a dictionary, list, string or any other type.
                        Dictionaries and lists will be traversed recursively.
            context (dict): Dictionary containing values to use for template string resolution.

        Returns:
            Any: A new bundle with all template strings resolved using the context.
                The structure of the original bundle is preserved.

        Examples:
            >>> context = {'name': 'John'}
            >>> resolve_bundle({'greeting': 'Hello ${name}'}, context)
            {'greeting': 'Hello John'}
            >>> resolve_bundle(['Hi ${name}', 123], context)
            ['Hi John', 123]
        """
        if isinstance(bundle, dict):
            return {k: cls._resolve_bundle(v, context) for k, v in bundle.items()}
        elif isinstance(bundle, list):
            return [cls._resolve_bundle(item, context) for item in bundle]
        elif isinstance(bundle, str):
            return cls._resolve_string(bundle, context)
        else:
            return bundle

    @classmethod
    def _collect_missing_tokens(cls, bundle: Any) -> set:
        """
        Recursively scans the bundle for unresolved tokens (by matching ${...} patterns)
        and returns a set of missing token strings.
        """
        tokens = set()
        if isinstance(bundle, dict):
            for value in bundle.values():
                tokens.update(cls._collect_missing_tokens(value))
        elif isinstance(bundle, list):
            for item in bundle:
                tokens.update(cls._collect_missing_tokens(item))
        elif isinstance(bundle, str):
            tokens.update(set(re.findall(r"\$\{([^}]+)\}", bundle)))
        return tokens

    @staticmethod
    def _update_bundle_with_current_user(bundle: dict) -> dict:
        """Updates the bundle dictionary with current user information.

        This function retrieves the current user's information from the Workspace API and adds it to
        the provided bundle dictionary under the 'workspace.current_user' key. It also creates a
        'short_name' field by extracting the username part before the '@' symbol, and adds a
        'userName' field that matches the 'user_name' value.

        Args:
            bundle (dict): The bundle dictionary to be updated with current user information.

        Returns:
            dict: The updated bundle dictionary containing current user information under
                'workspace.current_user'. Returns the original bundle if an error occurs.

        Raises:
            No exceptions are raised as they are caught and logged internally.
        """
        try:
            w = WorkspaceClient()
            current_user = w.current_user.me().__dict__
            current_user["short_name"] = current_user["user_name"].split("@")[0]
            bundle["workspace"]["current_user"] = current_user
            bundle["workspace"]["current_user"]["userName"] = current_user["user_name"]
        except Exception as e:
            logger.exception(f"Error updating bundle with current user: {e}")
        return bundle

    @staticmethod
    def _update_bundle_with_git(bundle: dict, spark) -> dict:
        """Updates bundle configuration with Git information based on the run context.

        This function attempts to extract Git-related metadata from the Databricks notebook context
        when running in a Databricks workspace. When running locally, it retrieves Git information
        from the local Git repository.

        Args:
            bundle (dict): The bundle configuration dictionary to be updated.
            spark: The Spark session object used to get Databricks utilities.

        Returns:
            dict: The updated bundle configuration with Git information added under
                bundle['bundle']['git']. If an error occurs during the update, returns
                the original bundle unchanged.

        Note:
            - In a Databricks workspace, Git information is extracted from MLflow context variables.
            - Locally, Git information is retrieved using the `git` command-line tool.
        """
        try:
            run_context = get_databricks_run_context(spark)

            if run_context == RunContextEnum.workspace:
                # Databricks workspace logic
                dbutils = get_dbutils(spark)
                context = json.loads(
                    dbutils.notebook.entry_point.getDbutils()
                    .notebook()
                    .getContext()
                    .toJson()
                )
                bundle["bundle"]["git"] = {
                    "actual_branch": context["extraContext"].get("mlflowGitReference"),
                    "branch": context["extraContext"].get("mlflowGitReference"),
                    "commit": context["extraContext"].get("mlflowGitCommit"),
                    "origin_url": context["extraContext"].get("mlflowGitUrl"),
                }
            elif run_context == RunContextEnum.local:
                # Local environment logic
                branch = subprocess.check_output(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"], text=True
                ).strip()
                commit = subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], text=True
                ).strip()
                origin_url = subprocess.check_output(
                    ["git", "config", "--get", "remote.origin.url"], text=True
                ).strip()

                bundle["bundle"]["git"] = {
                    "actual_branch": branch,
                    "branch": branch,
                    "commit": commit,
                    "origin_url": origin_url,
                }
            else:
                logger.warning(
                    "Unsupported run context. Git information will not be updated."
                )
        except subprocess.CalledProcessError as e:
            logger.exception(f"Error retrieving Git information locally: {e}")
        except Exception as e:
            logger.exception(f"Error updating bundle with Git information: {e}")

        return bundle

    @staticmethod
    def _find_default_target(targets: dict) -> str:
        """
        Finds the default target from a dictionary of targets.

        Args:
            targets (dict): A dictionary where keys are target names and values are configurations.

        Returns:
            str: The name of the default target if found, otherwise "dev".
        """
        for target_name, config in targets.items():
            if config.get("default", False) is True:
                return target_name
        return "dev"

    @classmethod
    def build(cls, spark, obj: Any) -> Any:
        if isinstance(obj, dict):
            new_data = {k: cls.build(spark, v) for k, v in obj.items()}
            return cls(spark, **new_data)
        elif isinstance(obj, list):
            return [cls.build(spark, elem) for elem in obj]
        else:
            return obj

    @classmethod
    def build_with_bundle_cli(cls, spark, target=None) -> BetterBundleConfig:
        """
        Builds a BetterBundleConfig instance using the Databricks bundle CLI.

        This method runs the 'databricks bundle validate' command to get bundle configuration
        and processes it into a BetterBundleConfig object.

        Args:
            spark: The active Spark session
            target (str, optional): The bundle target to validate. If not provided, uses default target.

        Returns:
            BetterBundleConfig: A configured instance based on bundle CLI output.
            None: If not running in local context.

        Raises:
            subprocess.CalledProcessError: If the databricks bundle validate command fails
            json.JSONDecodeError: If the command output cannot be parsed as JSON

        Example:
            >>> config = BetterBundleConfig.build_with_bundle_cli(spark, target="development")
        """
        # Builds the config using the databricks bundle CLI.
        run_context = get_databricks_run_context(spark)

        if not run_context == RunContextEnum.local:
            logger.error(
                f"Running in {run_context.value} context. Databricks bundle CLI is not available."
            )

            return

        try:
            if target:
                result = subprocess.run(
                    [
                        "databricks",
                        "bundle",
                        "validate",
                        "--output",
                        "json",
                        "--target",
                        target,
                    ],
                    capture_output=True,
                    text=True,
                )
            else:
                logger.warning("No target provided. Using default target")
                result = subprocess.run(
                    ["databricks", "bundle", "validate", "--output", "json"],
                    capture_output=True,
                    text=True,
                )
            bundle = json.loads(result.stdout)
            bundle = cls._process_variables(bundle)
        except subprocess.CalledProcessError as e:
            logger.exception(f"Error running databricks bundle validate: {e}")
            raise
        except json.JSONDecodeError as e:
            logger.exception(
                f"Error decoding JSON from databricks bundle validate output: {e}"
            )
            raise

        return cls.build(spark, bundle)

    @classmethod
    def build_with_bundle_yml(
        cls,
        spark,
        target=None,
        bundle_path=None,
        validate: bool = True,
        raise_on_missing_token: bool = False,
    ) -> BetterBundleConfig:
        """Builds a BetterBundleConfig instance using a Databricks bundle YAML configuration file.

        This method loads and processes a databricks.yml bundle configuration file, optionally validates it
        using the Databricks CLI, and constructs a BetterBundleConfig object with the processed configuration.

        Args:
            spark: A SparkSession object used for configuration context.
            target: Optional[str], the target environment to use from the bundle configuration.
                If None, will attempt to use the default target.
            bundle_path: Optional[str], path to the databricks.yml file.
                If None, will search for the file in the current directory structure.
            validate: bool, whether to validate the bundle using the Databricks CLI.
                Defaults to True. Note that validation is skipped in non-local environments.
            raise_on_missing_token: bool, whether to raise an exception when encountering missing
                variable tokens during resolution. Defaults to False.

        Returns:
            BetterBundleConfig: A configured instance containing the processed bundle configuration.

        Raises:
            FileNotFoundError: If the databricks.yml file cannot be found.
            yaml.YAMLError: If there are issues parsing the YAML configuration.

        Note:
            The method performs several processing steps including:
            - Bundle validation (if enabled)
            - Target resolution and application
            - Current user information updates
            - Git information updates
            - Variable resolution
            - Nested reference resolution
        """
        # Builds the config using the databricks bundle CLI.
        run_context = get_databricks_run_context(spark)

        if not run_context == RunContextEnum.local and validate:
            logger.info(f"""`validate` set to True but run context is {run_context.value}. 
                        Databricks bundle CLI is not available to validate the bundle....skipping validation.""")

        elif validate:
            logger.info(
                """`validate` set to True. Running databricks bundle validate..."""
            )

            if target:
                result = subprocess.run(
                    [
                        "databricks",
                        "bundle",
                        "validate",
                        "--target",
                        target,
                    ],
                    capture_output=True,
                    text=True,
                )
            else:
                logger.warning("No target provided. Using default target")
                result = subprocess.run(
                    ["databricks", "bundle", "validate"],
                    capture_output=True,
                    text=True,
                )

            if result.returncode != 0:
                logger.error(
                    result.stdout + "Please fix the bundle or disable validation."
                )

                return

            else:
                logger.success(result.stdout)

        try:
            if not bundle_path:
                logger.info(
                    "No bundle path provided. Searching for databricks.yml file."
                )
                bundle_path = find_databricks_yml()
                logger.info(f"Found databricks.yml file: {bundle_path}")

            bundle = cls._load_bundle(bundle_path)

            if not target:
                if "targets" in bundle:
                    default_target = cls._find_default_target(bundle["targets"])
                    logger.info(
                        f"No target provided. Using default target: {default_target}"
                    )
                    bundle = cls._apply_target_bundle(bundle, default_target)
            else:
                bundle = cls._apply_target_bundle(bundle, target)

            bundle = cls._update_bundle_with_current_user(bundle)
            bundle = cls._update_bundle_with_git(bundle, spark)
            bundle = cls._process_variables(bundle)
            bundle = cls._resolve_bundle(
                bundle=bundle,
                context=bundle,
            )

            # Additional passes to resolve nested or self-references completely
            previous = None
            while previous != bundle:
                previous = bundle
                bundle = cls._resolve_bundle(bundle, context=bundle)

            # Final pass to collect missing tokens and log a single warning.
            missing = cls._collect_missing_tokens(bundle)
            if missing:
                message = "Missing substitution(s): " + ", ".join(sorted(missing))

                if raise_on_missing_token:
                    raise ValueError(message)
                else:
                    logger.warning(message)

        except FileNotFoundError as e:
            logger.exception(f"databricks.yml file not found: {e}")
            raise
        except yaml.YAMLError as e:
            logger.exception(f"Error loading YAML configuration: {e}")
            raise

        return cls.build(spark, bundle)

    def get_with_widgets(self, name: str, default: any = None) -> any:
        try:
            dbutils = get_dbutils(self.spark)
            value = dbutils.widgets.get(name)
        except Exception:
            logger.warning(f"Widget '{name}' not found. Using config fallback.")
            return self.get(name, default)
    
        if value.lower() in ["true", "false"]:
            return value.lower() == "true"
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value

    def get(self, name: str, default: Any = None) -> Any:
        try:
            return getattr(self, name, default)
        except AttributeError:
            return default

    def __repr__(self, indent=0):
        def format_value(value, indent):
            if isinstance(value, BetterBundleConfig):
                return value.__repr__(indent + 2)
            elif isinstance(value, list):
                return f"[{', '.join(map(str, value))}]"
            elif isinstance(value, dict):
                return f"{{{', '.join(f'{k}: {v}' for k, v in value.items())}}}"
            else:
                return repr(value)

        indent_str = " " * indent
        fields = ",\n  ".join(
            f"{indent_str}{name}={format_value(val, indent)}"
            for name, val in self.__dict__.items()
        )
        return f"{indent_str}Config(\n  {fields}\n{indent_str})"


if __name__ == "__main__":
    pass
    # pass
    # from databricks.connect import DatabricksSession

    # spark = DatabricksSession.builder.serverless().profile("DEFAULT").getOrCreate()

    # print(BetterBundleConfig.build_with_bundle_yml(spark, validate=False))
    # # print(BetterBundleConfig.build_with_bundle_cli(spark))
    # # print(BetterBundleConfig.build_with_bundle_cli(spark))
    # DatabricksSession
