# [Fix flag inference mechanism causing invalid flag combinations]

## Description
We are receiving reports from downstream projects (specifically Enaml) that our flag inference logic is raising `ValueError` for valid bytecode sequences. The issue appears to happen when bytecode contains instructions that imply conflicting or complex behaviors (e.g., mixing generator yields with async awaitables).

See the external report here for context: [nucleic/enaml#399](https://github.com/nucleic/enaml/issues/399)

Currently, `infer_flags` (and `update_flags`) generates invalid combinations of flags for these cases, triggering internal validation errors.

We need to refine the inference logic to handle these cases gracefully. Additionally, the current API lacks the necessary granularity to handle these ambiguities manually. The API should be updated to allow callers to explicitly control the async/sync enforcement strategy.

## Steps to Reproduce / Logs
The following pattern, which involves both `YIELD_VALUE` and `GET_AWAITABLE`, triggers the validation error:

```python
from bytecode import ConcreteBytecode, ConcreteInstr

code = ConcreteBytecode()
# Presence of both YIELD and AWAIT related instructions confuses the current inference
code.append(ConcreteInstr('YIELD_VALUE'))
code.append(ConcreteInstr('GET_AWAITABLE'))

# This raises ValueError
code.update_flags() 
```

**Stack Trace:**
```text
Traceback (most recent call last):
  ...
  File "bytecode/flags.py", line ..., in infer_flags
    raise ValueError("Code should not have more than one of the following flag set : generator, coroutine, iterable coroutine and async generator, got: %s" % flags)
ValueError: Code should not have more than one of the following flag set : generator, coroutine, iterable coroutine and async generator, got: ...
```

## Expected Behavior
1.  **Correct Inference:** The library should be able to handle bytecode with mixed async and yield instructions without crashing. It should correctly infer the appropriate flag (e.g., `ASYNC_GENERATOR`) rather than setting conflicting flags.
2.  **Explicit Control:** The `update_flags` and `infer_flags` API should be updated to allow users to strictly enforce the intended behavior:
    *   **Force Async:** Users should be able to explicitly force the code to be marked as asynchronous.
    *   **Validate Sync:** Users should be able to explicitly validate that code is synchronous (raising an error if async instructions are present).
    *   **Default:** There should be a default mode that relies on pure inference.

The repository is at `/workspace/bytecode`, checked out at commit `54d9ad7b43951a63a4bbcef95961c1eb1779ef00`.