# License policy notes

This file is generated. The editable policy is `LICENSE/audit-policy.json`.

## Project license

The project code is published under MIT, as recorded in the root `LICENSE.txt`.
This was selected for the public GitHub source repository because no prior project license was present.

## Classification policy

- `allowed`: the SPDX identifier is explicitly allowed and the distribution supplied license evidence.
- `conditional`: LGPL or an explicit dual-license choice; keep the license text and review binary distribution conditions.
- `blocked`: GPL/AGPL terms or an equivalent prohibited expression.
- `needs_review`: missing, unknown, custom, or otherwise unclassified evidence. The default gate blocks it.

Allowed SPDX identifiers:

- `0BSD`
- `Apache-2.0`
- `BSD-2-Clause`
- `BSD-3-Clause`
- `CC0-1.0`
- `HPND`
- `ISC`
- `MIT`
- `MIT-CMU`
- `MPL-2.0`
- `PSF-2.0`
- `Unicode-3.0`
- `Zlib`

## Current summary

```json
{
  "allowed": 27,
  "asset_needs_review": 0,
  "blocked": 0,
  "conditional": 6,
  "missing": 0,
  "needs_review": 0,
  "syntax_errors": 0,
  "undeclared_external_imports": 0
}
```

## Important boundaries

- The dependency report includes runtime, selected optional, and development dependency closures separately.
- A clean global installation is not a project lockfile. Pin versions in a lock/constraints file before binary distribution if reproducible deployment is required.
- Source PNGs and derived image fixtures are not covered by the code MIT license automatically. Their provenance entries remain a separate review surface.
- This repository does not contain an npm manifest, Cargo manifest, sidecar executable, font, audio, or video file at audit time.
