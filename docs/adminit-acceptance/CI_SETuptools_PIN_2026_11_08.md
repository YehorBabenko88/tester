# CI build-tool vulnerability repair

Exact source f582993 passed Linux3.14 platform, while Linux3.11 passed tests and
lifecycle then pip-audit reported setuptools79.0.1: PYSEC-2026-3447 (fixed83.0.0)
and GHSA-xmj3-9gf7-h52w (fixed84.1.0). Three audit findings include a duplicate
PYSEC identifier; this is not a claim of three distinct advisories.

Pin setuptools84.1.0 in build-system requires, the dev extra and the full lock.
The dev dependency is necessary: a constraint alone does not upgrade the ambient
setuptools inherited by Python3.11 runners. The isolated build backend is pinned
as well. No audit ignore, exclusion or success override is introduced.

Primary sources checked on8November2026:
- https://github.com/advisories/GHSA-xmj3-9gf7-h52w
- https://pypi.org/pypi/setuptools/84.1.0/json

PyPI reports requires_python>=3.10, not yanked and no vulnerability entries for
84.1.0. The wheel SHA256 is
5231689c6ac8156cd155cfb98c3fd6fb252c3e7e9bd3d78b64d3c55f132624c9.
Actual CI audit remains the acceptance gate. Installed native719 is untouched.
