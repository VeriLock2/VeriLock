# VeriLock Pi Version 1 — Release Scan Record

The final release scan ran on the demo Raspberry Pi using Grype 0.122.1 and the signed CycloneDX SBOM, revision 15. The [scan results](VeriLock-Dependency-Scan.json) and [Pi scan evidence](VeriLock-Release-Scan-Evidence.txt) identify the release inventory and scanner database.

**SBOM SHA-256** `f69ffa98338d87be0ee3be1095cb0832dd5b4660f3773f3a590f9dd16c49b889`

| Severity | Grype matches |
| --- | ---: |
| Critical | 96 |
| High | 500 |
| Medium | 415 |
| Low | 62 |
| Negligible | 203 |
| Unknown | 3 |
| **Total** | **1,279** |

Grype counts package and advisory matches across the Pi inventory. A package match identifies a version associated with an advisory. Confirming exposure requires checking the advisory conditions against the deployed configuration.
