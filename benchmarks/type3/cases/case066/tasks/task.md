# AzureOpenAI AuthenticationError in hybrid environments and parameter mismatch

## Description
We are encountering issues when using the `AzureOpenAI` integration in an environment where both standard OpenAI and Azure OpenAI credentials are present.

When running the application with both `OPENAI_API_KEY` and `AZURE_OPENAI_API_KEY` set, requests to the Azure endpoint fail with an `AuthenticationError` (401). It appears the client might be attempting to use the wrong credential for the Azure endpoint.

Additionally, the current `AzureOpenAI` constructor parameters (specifically `api_base`) do not align with the terminology found in the recent Azure OpenAI SDK documentation. This discrepancy causes confusion regarding proper endpoint configuration.

## Reproduction Steps
1.  Set up an environment with both `OPENAI_API_KEY` (valid OpenAI key) and `AZURE_OPENAI_API_KEY` (valid Azure key).
2.  Initialize the `AzureOpenAI` class using the current parameters.
3.  Attempt to execute a model request.

## Observed Behavior
The request fails with a 401 Unauthorized error, suggesting an authentication failure against the Azure endpoint.

## Task
1.  Investigate and resolve the credential conflict to ensure `AzureOpenAI` correctly authenticates using Azure credentials when both sets of keys are present.
2.  Review the `AzureOpenAI` implementation against the current Azure OpenAI v1 SDK and update the constructor parameters and internal configuration to match current upstream conventions.
3.  Verify that internal flags regarding model types (chat vs. completion) are accurate for the Azure implementation.
4.  Update documentation and `examples/with_azure.py` to reflect any API changes.
