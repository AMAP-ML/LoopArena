# Support Pydantic V2 with Backwards Compatibility

## Context
Pydantic V2 has been released. Currently, this repository relies on Pydantic V1 APIs.
When `pydantic>=2.0` is installed, the library fails with runtime errors (e.g., `AttributeError`) because the codebase attempts to access attributes or methods that are no longer available or behave differently in the new version.

## Observed Symptoms
Attempting to run the application or tests with Pydantic V2 results in crashes. The following areas appear to be affected:
*   **Serialization:** Operations involving model exporting fail.
*   **Parsing:** Instantiating models from objects raises errors.
*   **Configuration:** Existing model configuration settings trigger exceptions.

## Goal
Update the codebase to support Pydantic V2 while maintaining stability for the library.

*   **Compatibility:** Ensure the library functions correctly when `pydantic>=2.0` is installed.
*   **Testing:** Update the build system and test environments to verify the library against Pydantic V2. Existing tests must pass under the new environment.

The repository is at `/workspace/fastapi-users`, checked out at commit `3bf0f888ee1847f52b8c5a96e30a1c90ea0f36e9`.