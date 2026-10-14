# Support `plaster` for configuration loading

## Description
Pyramid's configuration loading is currently tightly coupled to `PasteDeploy`, which restricts users to `.ini` files. We aim to decouple the framework from specific file parsers to support alternative configuration formats (e.g., YAML, TOML) in the future.

The goal of this task is to migrate the core loading logic in `pyramid.paster` and the associated command-line scripts (`pserve`, `pshell`, `proutes`, etc.) to use the **`plaster`** abstraction library instead of calling `paste.deploy` directly.

Relevant discussions can be found in issues #1394 and #2419.

## Requirements
1.  **Refactor Loading Logic**: Update `pyramid.paster` (specifically functions like `get_app`, `get_appsettings`, and `setup_logging`) to utilize `plaster` for configuration loading.
2.  **CLI Updates**: Ensure command-line utilities (`pserve`, `pshell`, etc.) are updated to retrieve configuration and WSGI apps via the new mechanism.
3.  **Backward Compatibility**: Existing standard `PasteDeploy` INI files **must** continue to work exactly as before. The migration should not break existing user workflows relying on INI configuration.
4.  **Logging**: The current logging setup manually parses configuration files. This should be aligned with the new abstraction where appropriate.

## Context & Resources
The current implementation in `pyramid/paster.py` imports `paste.deploy` directly:

```python
from paste.deploy import loadapp, appconfig

def get_app(config_uri, name=None, options=None):
    # ... logic ...
    return loadapp(config_uri, name=name, global_conf=options)
```

Documentation for the `plaster` library is available here:
http://docs.pylonsproject.org/projects/plaster/en/latest/

Please investigate the `plaster` documentation to determine the appropriate API usage and necessary dependencies to fulfill the requirements.

The repository is at `/workspace/pyramid`, checked out at commit `ab04b7be473d0145d9f306583c4d529ee8abbe02`.