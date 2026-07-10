# Source and implementation provenance (R0)

This product is independently implemented. Reference projects are used only to
study capability coverage and architecture; their source is not part of the
product build context or release artifact.

| Reference | Snapshot evidence | Use | Boundary |
| --- | --- | --- | --- |
| 1Panel | upstream `https://github.com/1Panel-dev/1Panel.git`, commit `270ba799f720f1415d106a3421e58018b31494b0` dated 2026-07-09 | control-plane/Agent separation, typed capabilities, task/event patterns, Docker/Nginx/DB coverage | no source copied; direct reuse/modification requires separate GPLv3 review |
| BaoTa local reference | local snapshot under `demo/BaoTa`; upstream version/commit not established | capability inventory and operational scenario research | license restricts copying/modification/derivatives; no business code, plugin loader or binary copied |
| Internal research | `demo/底层能力研究报告.md` | summarized architecture/capability comparison | design input only; production code remains independent |

The default-deny `.dockerignore` includes only application source, package
manifests and Nginx build config. It excludes the complete `demo/` tree. The
production `.gitignore` also excludes reference source checkouts from the
product repository baseline.

## Release provenance gate

R0 local images are unsigned and do not yet have SBOM/provenance attestations.
GA remains `NO-GO` until CI records source commit, build invocation, dependency
lockfiles, image digest, SPDX/CycloneDX SBOM, vulnerability results and a
verified signature for every artifact. Any source whose license/version cannot
be established is excluded from the release.
