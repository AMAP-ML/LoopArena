# Support Happy Eyeballs (RFC 8305) in TCPConnector

## Description
We are observing significant latency when connecting to dual-stack hosts (IPv4/IPv6) in environments where the IPv6 path is unresponsive or drops packets (blackholed). Currently, the connection logic appears to attempt addresses sequentially, waiting for the full socket timeout on the first address (often IPv6) before attempting the next.

We need to update `TCPConnector` to support the "Happy Eyeballs" algorithm (RFC 8305) to mitigate this latency. The implementation should allow for parallel connection attempts with a staggered delay.

**Requirements:**
1.  Update `TCPConnector` to accept `happy_eyeballs_delay` (defaulting to 0.25s) and `interleave` parameters.
2.  Ensure that connection attempts adhere to RFC 8305 principles, utilizing the resolved addresses to establish a connection as quickly as possible.

**Relevant Resources:**
*   RFC 8305: Happy Eyeballs Version 2
*   Library: https://github.com/bdraco/aiohappyeyeballs (This library may be relevant for handling Happy Eyeballs logic with pre-resolved addresses).

## Steps to Reproduce
1.  Create a `TCPConnector`.
2.  Attempt to `GET` a URL where the DNS resolves to a reachable IPv4 address and an unreachable IPv6 address.
3.  **Observation:** The request takes a long time (equal to the OS TCP timeout) before succeeding.

The following script simulates the issue (assuming the environment or mock setup causes the first IP to timeout):

```python
import asyncio
import aiohttp
import time

async def main():
    # Current behavior: Waits for full timeout on bad IP if it appears first in resolution
    async with aiohttp.ClientSession() as session:
        t0 = time.monotonic()
        try:
            # If 'example.com' resolves to [Unreachable_IPv6, Reachable_IPv4]
            # aiohttp currently tries IPv6 -> Wait Timeout -> Fail -> Try IPv4
            await session.get('http://example.com') 
        except Exception:
            pass
        print(f"Time taken: {time.monotonic() - t0:.2f}s")
        # Actual Result: Time taken is usually > 30s (depending on OS timeout)
        # Expected Result: Time taken should be ~0.25s + TCP RTT

if __name__ == "__main__":
    asyncio.run(main())
```

## Expected Behavior
The `TCPConnector` should utilize the new parameters to perform staggered connection attempts.

**Usage Example:**

```python
# The connector should support these new arguments
connector = aiohttp.TCPConnector(happy_eyeballs_delay=0.25, interleave=1)

async with aiohttp.ClientSession(connector=connector) as session:
    # Should connect quickly even if the preferred IPv6 address is silent
    await session.get('http://example.com')
```

The repository is at `/workspace/aiohttp`, checked out at commit `23931465361cc366b97c1ef1b6419ea8dd2642a4`.