# Update dependency compatibility for NumPy 1.24+ and DScribe

## Description
We are updating the repository to support modern versions of core dependencies, specifically **NumPy 1.24+** and the latest release of **DScribe**.

Currently, running the test suite in this environment results in multiple errors, including TypeErrors and assertion failures. We need to resolve these compatibility issues.

Additionally, we want to update our project metadata to follow the minimum version support guidelines recommended by [SPEC 0000](https://scientific-python.org/specs/spec-0000/). Please also review **Pandas** version constraints to ensure stability.

## Current Behavior
When running `pytest` or `tox` with **NumPy 1.24+** installed, the following issues occur:

1.  **Error in Featurizers:**
    Utilities in `matminer.featurizers.utils.grdf` crash with the following traceback:
    ```
    TypeError: logical_and() got an unexpected keyword argument 'dtype'
    ```

2.  **DScribe Test Failures:**
    Integration tests involving DScribe are failing.

3.  **Numerical Mismatches:**
    Tests for `WenAlloy` are failing value assertions.

## Expected Behavior
1.  The codebase should run successfully with **NumPy 1.24+** and the latest **DScribe**.
2.  All tests, including `WenAlloy` and DScribe integrations, should pass.
3.  Project metadata should align with SPEC 0 recommendations.

The repository is at `/workspace/matminer`, checked out at commit `36cbe8b61911fcee9c8a3489f2ceda2414dc86db`.