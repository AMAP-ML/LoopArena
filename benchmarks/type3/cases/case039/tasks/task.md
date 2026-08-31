# [Feature]: Optimize `CookieJar.filter_cookies` performance

## Description
The current implementation of `CookieJar.filter_cookies` has been identified as a performance bottleneck when the cookie jar contains a large number of items. It currently iterates over every single stored cookie to check for matches, resulting in **O(N)** complexity.

We need to refactor this method to improve efficiency. The goal is to reduce the computational complexity of retrieving cookies for a specific request URL, avoiding the need to iterate through unrelated cookies in the jar.

Relevant discussion and context can be found here:
https://github.com/aio-libs/aiohttp/issues/7583

## Steps to Reproduce
The following script demonstrates the functional requirements for cookie filtering across various domain and path combinations. The optimized implementation must pass these checks to ensure no regression in logic.

```python
import asyncio
from http.cookies import SimpleCookie
from aiohttp.cookiejar import CookieJar
from yarl import URL

async def reproduce():
    jar = CookieJar()
    # Setup a complex set of cookies
    cookies = SimpleCookie(
        "shared-cookie=first; "
        "domain-cookie=second; Domain=example.com; "
        "subdomain1-cookie=third; Domain=test1.example.com; "
        "subdomain2-cookie=fourth; Domain=test2.example.com; "
        "dotted-domain-cookie=fifth; Domain=.example.com; "
        "different-domain-cookie=sixth; Domain=different.org; "
        "secure-cookie=seventh; Domain=secure.com; Secure; "
        "no-path-cookie=eighth; Domain=pathtest.com; "
        "path1-cookie=ninth; Domain=pathtest.com; Path=/; "
        "path2-cookie=tenth; Domain=pathtest.com; Path=/one; "
        "path3-cookie=eleventh; Domain=pathtest.com; Path=/one/two; "
        "path4-cookie=twelfth; Domain=pathtest.com; Path=/one/two/; "
        "expires-cookie=thirteenth; Domain=expirestest.com; Path=/;"
        " Expires=Tue, 1 Jan 1980 12:00:00 GMT; "
        "max-age-cookie=fourteenth; Domain=maxagetest.com; Path=/;"
        " Max-Age=60; "
        "invalid-max-age-cookie=fifteenth; Domain=invalid-values.com; "
        " Max-Age=string; "
        "invalid-expires-cookie=sixteenth; Domain=invalid-values.com; "
        " Expires=string;"
    )
    jar.update_cookies(cookies)

    # Define test cases: (URL, Expected Cookie Names)
    test_cases = [
        (
            "http://pathtest.com/one/two/",
            {
                "no-path-cookie",
                "path1-cookie",
                "path2-cookie",
                "shared-cookie",
                "path3-cookie",
                "path4-cookie",
            },
        ),
        (
            "http://pathtest.com/one/two",
            {
                "no-path-cookie",
                "path1-cookie",
                "path2-cookie",
                "shared-cookie",
                "path3-cookie",
            },
        ),
        (
            "http://pathtest.com/one/two/three/",
            {
                "no-path-cookie",
                "path1-cookie",
                "path2-cookie",
                "shared-cookie",
                "path3-cookie",
                "path4-cookie",
            },
        ),
        (
            "http://test1.example.com/",
            {
                "shared-cookie",
                "domain-cookie",
                "subdomain1-cookie",
                "dotted-domain-cookie",
            },
        ),
        (
            "http://pathtest.com/",
            {
                "shared-cookie",
                "no-path-cookie",
                "path1-cookie",
            },
        ),
    ]

    print("Verifying cookie filtering logic...")
    for url_str, expected_names in test_cases:
        cookies = jar.filter_cookies(URL(url_str))
        # filter_cookies returns a SimpleCookie-like object, iterating yields keys
        actual_names = set(cookies)

        assert actual_names == expected_names, \
            f"Failed for {url_str}.\nExpected: {expected_names}\nGot: {actual_names}"

    print("All functional tests passed.")

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    loop.run_until_complete(reproduce())
    loop.close()
```

## Expected Behavior
The `CookieJar.filter_cookies` method should be refactored to avoid full iteration over the cookie storage.

The implementation must maintain strict adherence to RFC 6265 behavior regarding domain and path matching, ensuring that the set of returned cookies is identical to the set returned by the current implementation (as verified by the reproduction script).
