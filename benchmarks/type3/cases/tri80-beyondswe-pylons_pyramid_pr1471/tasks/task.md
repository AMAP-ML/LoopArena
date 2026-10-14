# Aggregate Code Coverage across Python 2 and 3 contexts

## Description
Currently, our test coverage metrics rely on manually excluding Python 3 specific code using `# pragma: no cover` markers (e.g., in `pyramid/compat.py`). This prevents us from verifying if compatibility code is actually being tested.

We aim to update our testing infrastructure (`tox.ini` and `.travis.yml`) to collect coverage data from all test environments and combine them into a single aggregate metric.

Reference strategy for multi-environment coverage: [http://blog.ionelmc.ro/2014/05/25/python-packaging/](http://blog.ionelmc.ro/2014/05/25/python-packaging/).

Additionally, `pyramid/tests/test_scripts/pystartup.py` is currently causing interference and noise during coverage collection. This file is utilized by `test_pshell.py` for testing startup script execution. Please investigate the issues arising from this file during coverage runs and implement a fix that resolves the interference while maintaining the functionality of the tests.

## Steps to Reproduce
1. Inspect `pyramid/compat.py` and observe the `if PY3:` blocks marked with `# pragma: no cover`.
2. Run the current test suite via `tox`.
3. Observe that coverage is generated per environment, and there is no mechanism to merge results from Python 2 and Python 3 runs.

## Expected Behavior
*   `tox` configuration is updated to generate coverage data for distinct Python versions and combine them into an aggregate report.
*   `# pragma: no cover` markers are removed from version-specific blocks (like `PY3` checks) in `pyramid/compat.py` and other files, as the aggregate report should now cover them.
*   The interference caused by `pyramid/tests/test_scripts/pystartup.py` during coverage runs is resolved without breaking `test_pshell.py`.

The repository is at `/workspace/pyramid`, checked out at commit `17997b33190056919395bda336f531a3b8bce057`.