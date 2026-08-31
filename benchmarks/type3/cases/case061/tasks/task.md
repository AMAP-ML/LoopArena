# Update Django version support matrix and CI configuration

## Description
The project configuration (`setup.py`, `tox.ini`, and CI workflows) currently targets a range of Django versions. We are reviewing our dependency matrix to align with the official upstream support lifecycle. We need to identify which configured versions are no longer maintained and remove them to optimize the CI pipeline.

Additionally, it has been observed that the code coverage generation step is running in an environment pinned to one of the older Django versions (`django22`), which may not accurately reflect behavior on modern supported versions.

## Reproduction Script
```python
# This script inspects the tox configuration to list defined environments.
import configparser

config = configparser.ConfigParser()
config.read('tox.ini')
envlist = config['tox']['envlist']

print(f"Current tox envlist: {envlist}")
# Example output: py36-django22, py37-django30, py38-django31, py39-django32, ...
```

## Actual Behavior
The `tox` environment list and `setup.py` classifiers include entries for `django22`, `django30`, and `django31`. CI jobs are currently triggered for these versions.

## Expected Behavior
The project should only support Django versions that are currently receiving upstream updates. The test matrix and package classifiers should be updated to reflect this. The coverage check environment should be migrated to a supported Django version.
