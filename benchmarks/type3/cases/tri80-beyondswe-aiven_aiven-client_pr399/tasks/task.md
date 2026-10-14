# Add support for managing organization VPCs

## Description
We are introducing support for **Organization VPCs** (currently in Limited Availability).

We need to update the `aiven-client` to support this new feature. Please implement the necessary CLI commands and API interactions to match the workflows and specifications documented here:

https://aiven.io/docs/platform/howto/manage-organization-vpc

## Steps to Reproduce / Logs
Currently, the CLI does not recognize the commands associated with this feature.

```bash
$ avn organization vpc list --organization-id my-org
Error: unknown command "vpc" for "avn organization"
```

## Expected Behavior
The `aiven-client` should be updated to support the Organization VPC feature. The CLI command structure, arguments, and underlying API logic should align with the usage examples and requirements found in the linked documentation.

The repository is at `/workspace/aiven-client`, checked out at commit `af58051f40dc41b3ad228cbdd4e8c1c71c3a51b6`.