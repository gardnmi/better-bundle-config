<p align="center">
  <img src="assets/logo.png" alt="Logo" width="300">
</p>

**BetterBundleConfig** is a Python class designed to simplify how you manage and pass parameters in Databricks Bundle deployments. In standard deployments, you typically need to define parameters in two places:

1. **YAML Variables**: Where you set default values and (optionally) types.
2. **Databricks Job Parameters**: Passed as notebook widgets (`dbutils.widgets`), which only return strings.

This dual-definition pattern leads to:

- **Duplication**: You repeat parameter names and defaults in multiple places.
- **Type Issues**: Since widget values are strings, you must manually cast them back to their intended types.
- **Limited Defaults**: `dbutils.widgets` has no built-in default mechanism, making it inconvenient for users.

**BetterBundleConfig** solves these challenges by **centralizing configuration** (defining parameters in **one** place: your YAML files), **supporting type resolution**, and **integrating seamlessly** with widgets when necessary.

---

## Table of Contents

- [Table of Contents](#table-of-contents)
- [Installation](#installation)
- [Key Features](#key-features)
- [How It Works](#how-it-works)
- [Example YAML Configuration](#example-yaml-configuration)
  - [`databricks.yml`](#databricksyml)
  - [`variable.yml`](#variableyml)
- [Primary Usage Example](#primary-usage-example)
- [Local Usage with Databricks CLI](#local-usage-with-databricks-cli)
- [Complex Object Handling](#complex-object-handling)
- [Parameter Retrieval with Widgets](#parameter-retrieval-with-widgets)
- [Additional Examples](#additional-examples)
  - [1. Overriding Variables at Runtime](#1-overriding-variables-at-runtime)
  - [2. Accessing Git and User Data](#2-accessing-git-and-user-data)
  - [3. Printing the Entire Merged Config](#3-printing-the-entire-merged-config)
- [Summary](#summary)

---

## Installation

You can install **BetterBundleConfig** directly from PyPI using pip:

```bash
pip install better-bundle-config
```

## Key Features

- **Single Source of Truth**  
  All parameters reside in your YAML files. No more duplicating them across bundle and job configurations.

- **Consistent Type Handling**  
  Variables are automatically cast to according to how their are defined in the yml.

- **Widget Integration**  
  Seamlessly retrieve parameters from widgets when running inside a Databricks Job, or fall back to YAML defaults defined in your bundle.

- **Complex Object Support**  
  Nested dictionaries and lists (e.g., a full task configuration) can be safely defined in the YAML and resolved at runtime.

---

## How It Works

**BetterBundleConfig** offers two main ways to build a configuration object:

1. **`build_with_bundle_yml` (Primary)**  
   - Loads one or more YAML files (including any `include` directives).  
   - Merges target-specific configurations (e.g., `dev`, `staging`, `prod`).  
   - Recursively resolves all variable references.  
   - Optionally validates your bundle via `databricks bundle validate` (if local).  
   - Recommended for both **Databricks** and **local** usage.  

2. **`build_with_bundle_cli`**  
   - Uses the Databricks CLI command `databricks bundle validate --output json`.  
   - **Local-only** since Databricks Bundle CLI commands are not available within the Databricks environment.  
   - Not commonly needed if you’re working in Databricks notebooks/jobs.

Under the hood, **BetterBundleConfig** merges multiple YAML files, applies target-specific overrides, and then replaces all variable placeholders (e.g., `${var.my_value}`) with actual values.  It tries its best to faithfully re-create the datbricks bundle cli behavior, but it is not a 1:1 match.

---

## Example YAML Configuration

Below are two illustrative YAML files. The first (`databricks.yml`) defines high-level bundle structure and includes additional YAML files. The second (`variable.yml`) defines the parameters (variables) for multiple targets.

### `databricks.yml`

```yaml
bundle:
  name: better_bundle_config
  uuid: 9d208a75-eddc-4342-b883-40f3cdbaccf8

include:
  - src/better_bundle_config/*.yml  # <-- You can include variable.yml or others here

targets:
  dev:
    mode: development
    default: true
    workspace:
      host: www.dev.databricks.com

  staging:
    presets:
      name_prefix: "[staging] "
      pipelines_development: false
      trigger_pause_status: UNPAUSED
      tags:
        staging: staging
    workspace:
      host: www.staging.databricks.com

  prod:
    mode: production
    workspace:
      host: www.prod.databricks.com
```

### `variable.yml`

```yaml
variables:
  cluster_id:
    description: The ID of an existing cluster.
    default: 1234-567890-abcde123

  notebook_path:
    description: The path to an existing notebook.
    default: ./hello.py

  source_catalog:
    description: The source catalog to use.
    default: ${bundle.target}_source_catalog

  target_catalog:
    description: The target catalog to use.

  bool:
    description: A boolean variable.

  string:
    description: A string variable.

  int:
    description: An integer variable.

  float:
    description: A float variable.

  my_task:
    description: Tasks grouped under a specific task in a workflow
    type: complex
    default:
      bool: ${var.bool}
      string: ${var.string}
      float: ${var.float}
      int: ${var.int}
      environment: ${bundle.target}
      catalog:
        source_catalog: ${var.source_catalog}
        target_catalog: ${var.target_catalog}
      my_loop:
        - A
        - B
        - C
      checkpoint_path: /Volumes/${var.my_task.catalog.target_catalog}/my_schema/my_volume/_checkpoint

targets:
  dev:
    variables:
      cluster_id: 1234-567890-abcde124
      target_catalog: dev_target_catalog
      bool: true
      string: "Hello, Development!"
      int: 42
      float: 3.14

  staging:
    variables:
      cluster_id: 1234-567890-abcde124
      target_catalog: staging_target_catalog
      bool: false
      string: "Hello, Staging!"
      int: 100
      float: 2.71

  prod:
    variables:
      cluster_id: 1234-567890-abcde124
      target_catalog: prod_target_catalog
      bool: true
      string: "Hello, Production!"
      int: 7
      float: 1.618
```

---

## Primary Usage Example

This is the **recommended** approach, suitable for both local development and Databricks jobs/notebooks.

```python
from mybundle import BetterBundleConfig
from databricks.connect import DatabricksSession

spark = DatabricksSession.builder.getOrCreate()

# 1. Build configuration from YAML files (e.g., "databricks.yml" + included files).
config = BetterBundleConfig.build_with_bundle_yml(
    spark,
    target="dev",                       # Which target to apply
    bundle_path="path/to/databricks.yml",
    validate=False,                      # Skip CLI validation if True is not desired or not local
    raise_on_missing_token=False         # If True, raises error when a variable is missing
)

# 2. Use the config
print("Cluster ID:", config.variables.cluster_id)
print("Notebook Path:", config.variables.notebook_path)
print("Source Catalog:", config.variables.source_catalog)
print("Target Catalog:", config.variables.target_catalog)
print("My Task Catalog:", config.variables.my_task.catalog.target_catalog)
```

**Output** (Sample):

```python
Cluster ID: 1234-567890-abcde124
Notebook Path: ./hello.py
Source Catalog: dev_source_catalog
Target Catalog: dev_target_catalog
My Task Catalog: dev_target_catalog
```

In addition, you can access the entire configuration object to see everything resolved:

```python
print("Built Configuration:", config)
```

---

## Local Usage with Databricks CLI

If you **only** want to rely on the `databricks bundle validate --output json` command **locally**, you can do:

```python
from mybundle import BetterBundleConfig
from databricks.connect import DatabricksSession

spark = DatabricksSession.builder.getOrCreate()

# This will internally call "databricks bundle validate --output json --target dev"
config = BetterBundleConfig.build_with_bundle_cli(spark, target="dev")

print("Config:", config)
```

**Note**: This method is **not** available inside Databricks environments—Databricks CLI commands can’t be executed there.

---

## Complex Object Handling

**BetterBundleConfig** excels at handling nested dictionaries and lists. In the above example, `my_task` is a complex object:

```yaml
my_task:
  type: complex
  default:
    bool: ${var.bool}
    ...
    my_loop:
      - A
      - B
      - C
    checkpoint_path: /Volumes/${var.my_task.catalog.target_catalog}/my_schema/my_volume/_checkpoint
```

Upon resolution, you can directly do:

```python
bool_val = config.variables.my_task.bool  # boolean
my_loop = config.variables.my_task.my_loop  # list -> ['A', 'B', 'C']
checkpoint = config.variables.my_task.checkpoint_path  # string -> /Volumes/dev_target_catalog/my_schema/...
```

All references like `${var.bool}` or `${bundle.target}` are automatically resolved, preserving structure and types. This means **no more manual parsing** or casting in your code.

---

## Parameter Retrieval with Widgets

When running inside a Databricks Job, you might define widgets in the **Job configuration**. BetterBundleConfig provides a helpful method:

```python
# Retrieve a parameter. Check dbutils.widgets first. If not present, fall back to config default.
value = config.get_with_widgets(name="some_param", default="fallback_value")
print("Param Value:", value)
```

**What this does**:

- Tries `dbutils.widgets.get("some_param")`. If it exists, it automatically casts booleans and JSON strings into Python types.
- If the widget doesn’t exist, it falls back to `config.get("some_param", default="fallback_value")`.

---

## Additional Examples

### 1. Overriding Variables at Runtime

Databricks allows you to set widget parameters when creating a job or running a notebook. For instance:

```python
dbutils.widgets.text("override_catalog", "override_val")
final_catalog = config.get_with_widgets("override_catalog", default=config.variables.target_catalog)
print("Final Catalog:", final_catalog)
```

- If the job param `override_catalog` was provided, you’ll see that value.
- Otherwise, it uses `config.variables.target_catalog`.

### 2. Accessing Git and User Data

If you’re working in a Databricks Notebook, BetterBundleConfig automatically populates Git metadata and the current Databricks user info when building with YAML:

```python
git_info = config.bundle.git
user_info = config.workspace.current_user

print("Git branch:", git_info.branch)
print("Current user:", user_info.user_name)
```

If you’re developing **locally**, Git data is read from your local repository. If you’re in the Databricks **workspace**, Git data is extracted from the MLflow context variables.

### 3. Printing the Entire Merged Config

You can see a fully expanded, merged, resolved config representation by:

```python
print("==== Full Config ====")
print(config)
```

This can be extremely useful for debugging or verifying that references are resolved correctly.

---

## Summary

**BetterBundleConfig** streamlines Databricks Bundle deployments by letting you:

1. **Define parameters only once** in YAML, avoiding duplication in `dbutils.widgets`.
2. **Automatically handle casting and defaults**, solving common type issues with string-based widgets.
3. **Leverage complex objects**, enabling sophisticated multi-step or nested configurations without manual JSON parsing.
4. **Integrate easily with Databricks Jobs**, using `get_with_widgets` to pick up job parameters at runtime.
