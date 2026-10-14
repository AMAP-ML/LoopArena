# Bug: `wait()` raises TimeoutExpired on already finished FakeProcess

## Description
We are experiencing a failure in `test_multiple_wait` within `pytest-subprocess`. The issue involves the behavior of `wait()` when called sequentially with timeouts.

Specifically, a `TimeoutExpired` exception is being raised for a process that appears to have already completed.

Please review the failure details and stack trace in the Codecov report here:
https://app.codecov.io/gh/aklajnert/pytest-subprocess/tests/fix_coverage

## Steps to Reproduce
1. Register a `FakeProcess` with a specific duration (e.g., 0.7s).
2. Call `process.wait(timeout=...)` multiple times, consuming parts of the duration.
3. Call `process.wait(timeout=...)` with a value large enough to exceed the remaining duration (finishing the process).
4. Assert that `process.returncode` is 0 (the process is done).
5. Call `process.wait(timeout=...)` one more time.

**Observed Symptom:**
The final `wait()` call raises `subprocess.TimeoutExpired`, even though the process is already dead.

## Expected Behavior
When interacting with a `FakeProcess`:
*   The process duration should be correctly accounted for across multiple `wait()` calls.
*   Once the cumulative wait time exceeds the registered process duration, the process should be marked as finished.
*   Subsequent calls to `wait()` on a finished process should **immediately return the exit code**, regardless of the `timeout` argument provided. They should **not** raise `TimeoutExpired`.
