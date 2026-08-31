# Data integrity issues with Debian package files on POSIX

## Description
We are observing data integrity issues in downstream projects (`scancode.io` and `extractcode`) when processing Debian package files. Resources associated with these packages are failing to link correctly.

Investigation suggests that `commoncode.paths.safe_path` and `commoncode.paths.portable_filename` are altering filenames that are valid in the Debian/POSIX context (e.g., `package:arch.list`), causing a mismatch between the expected and actual file paths on disk.

We need to adjust the behavior of these path utilities to support these valid POSIX filenames.

For further context on the downstream failures, refer to:
*   **Primary failure context:** [aboutcode-org/scancode.io#407](https://github.com/aboutcode-org/scancode.io/issues/407)
*   **Related investigation:** [aboutcode-org/extractcode#41](https://github.com/aboutcode-org/extractcode/issues/41)

## Steps to Reproduce
The following script demonstrates how valid Debian paths are currently being transformed.

```python
from commoncode.paths import safe_path, portable_filename

# A valid path on a Debian system
debian_path = 'var/lib/dpkg/info/libgsm1:amd64.list'

# Current behavior: The path is sanitized
sanitized = safe_path(debian_path)
print(f"Original: {debian_path}")
print(f"Sanitized: {sanitized}")

# Verification
if sanitized == debian_path:
    print("SUCCESS: Path preserved.")
else:
    print("FAILURE: Path was altered.")
```

**Current Output:**
```text
Original: var/lib/dpkg/info/libgsm1:amd64.list
Sanitized: var/lib/dpkg/info/libgsm1_amd64.list
FAILURE: Path was altered.
```

## Expected Behavior
1.  The reproduction script should pass, meaning `safe_path` and `portable_filename` must be capable of preserving characters that are valid in POSIX filenames (such as colons) when appropriate.
2.  The solution should ensure that the default sanitization behavior remains robust for general cross-platform compatibility, while enabling the preservation of these specific POSIX paths when required.

## Public API for POSIX-only behavior

Expose the opt-in consistently as a boolean `posix_only` argument, defaulting
to `False`:

```python
safe_path(path, posix=False, preserve_spaces=False, posix_only=False)
portable_filename(filename, preserve_spaces=False, posix_only=False)
```

With `posix_only=False`, preserve the existing cross-platform behavior. With
`posix_only=True`, retain characters and base names that are valid on POSIX but
restricted on Windows, including colons, backslashes, and Windows-reserved
names. In this mode `/` remains the POSIX directory separator and must still be
sanitized when it appears within a filename. NUL and control characters must
also remain sanitized. Existing `preserve_spaces` behavior remains in force in
both modes.
