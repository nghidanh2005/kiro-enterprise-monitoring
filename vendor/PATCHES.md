# Vendored sample and local patches

## Upstream

| | |
|---|---|
| Repository | [aws-samples/sample-kiro-user-analytics-quicksight-dashboard](https://github.com/aws-samples/sample-kiro-user-analytics-quicksight-dashboard) |
| Commit | `1f5f5b8d1ffe84776c4d8005eb0cdaf31e3882f5` (2026-10-09) |
| License | MIT-0 (`LICENSE` in the vendored folder) |
| Import method | `git -c core.autocrlf=false archive` of that commit, so files keep LF endings |
| Import check | all 50 files hashed with `git hash-object --no-filters`: 50/50 equal to the upstream blobs |
| Unmodified import | commit `d73fc39` on this repo, so `git diff d73fc39 -- vendor/` shows every local change |

`.gitattributes` sets `vendor/** -text` so a Windows checkout does not convert these files to CRLF. `deploy.sh` and `preflight.sh` do not run under bash with CRLF.

## SHA256 of the patched files, before patching

| File | SHA256 (upstream) |
|---|---|
| `cfn/01-data-layer.yaml` | `5991b1453c72236cafba203d9893fbdc19d554289769d914b0dba0246698dde2` |
| `cfn/03-identity-mapping.yaml` | `9a926993fc5a038a980a725171f065dd78ef2f4107a5dd5bd2a535c419651de7` |
| `scripts/deploy.sh` | `ad857299fd009dfce1ca3fcdc1c52c2fae327cce58b98522206fbd2676e24b43` |
| `scripts/preflight.sh` | `804e60cc696ad0d292aa635d4680201409dfdd0dc7846d1e3659b98253fbc1b2` |
| `scripts/identity_map_lambda.py` | `34a58a1795ddb00e82bc1d9c0dc9cb6311a30d494ed3c24f6c1dc9b3708476f8` |

## Patches

Each patch keeps upstream behaviour when its new input is empty. Every inserted block is marked `PATCH P<n> (kiro-enterprise-monitoring)`.

### P1. Report written by another account

- `cfn/01-data-layer.yaml`: new parameter `SourceAccountId` (default `""`, pattern `^$|^[0-9]{12}$`), new condition `HasSourceAccount` added to the existing `Conditions` block, and `RAW_PREFIX` uses `SourceAccountId` when set, else `AWS::AccountId`.
- `scripts/deploy.sh`: reads `KIRO_SOURCE_ACCOUNT_ID` (default `""`), passes `SourceAccountId=${KIRO_SOURCE_ACCOUNT_ID}` in both data-stack `--parameter-overrides` blocks, and `probe_prefix` uses `${KIRO_SOURCE_ACCOUNT_ID:-$ACCOUNT_ID}`. Header comment documents the variable.

### P2. Preflight when the report bucket is in another account

- `scripts/preflight.sh`: reads `KIRO_SOURCE_ACCOUNT_ID`. When it is set and differs from the caller account, step 5 prints a `WARN` and does not list the bucket. The person deploying has no list permission on the customer bucket by design; the normalizer invoke result after deploy is the real check. Otherwise step 5 is unchanged, including its `FAIL`.

### P3. ExternalId for the Identity Center role

- `scripts/identity_map_lambda.py`: `_idc_client` adds `ExternalId` to `sts.assume_role` only when `IDC_EXTERNAL_ID` is non-blank. Docstring documents the variable.
- `cfn/03-identity-mapping.yaml`: new parameter `IdcExternalId` (default `""`) wired to the Lambda environment as `IDC_EXTERNAL_ID`.
- `scripts/deploy.sh`: reads `IDC_EXTERNAL_ID` (default `""`) and passes `IdcExternalId=${IDC_EXTERNAL_ID}` to the identity-map stack.

Diff size against `d73fc39`: 5 files, 47 insertions, 9 deletions.

## Tests

New file `tests/test_crossaccount_patches.py`, 13 tests. Each patch is tested with its new input both set and empty.

| Run | Ran | Pass | Error | Skipped |
|---|---|---|---|---|
| Upstream, before patching | 145 | 134 | 10 | 1 |
| After patching | 158 | 147 | 10 | 1 |

- The 10 errors are the same in both runs, all in `test_versioning.TestTagMerge`. That test starts `bash` through Python `subprocess`, which on this Windows machine resolves to the WSL launcher in System32 instead of Git Bash and fails with a WSL RPC error. They do not touch any patched code path.
- Against the five upstream files restored byte-exact, 9 of the 13 new tests fail. The 4 that pass on upstream guard upstream behaviour that must survive (one `Conditions` block, the same-account `FAIL`, no `ExternalId` when the variable is empty or unset).
- `cfn-lint` 1.52.0 on all three templates: 0 findings before and after.
- `bash -n` on `deploy.sh` and `preflight.sh`: 0 errors after patching.
- `preflight.sh` run under Git Bash with a stub `aws` CLI: cross-account prints `[WARN] report bucket belongs to account ...` and makes 0 list calls on the bucket; same-account still prints `[FAIL] user_report not found` after 1 list call.

## Remaining account-pinned paths

`grep AWSLogs` over `cfn/`, `scripts/` and `athena/` (tests excluded):

- `cfn/01-data-layer.yaml`: `RAW_PREFIX` (patched) and two parameter descriptions.
- `scripts/deploy.sh`: `probe_prefix` (patched) and a comment.
- `scripts/preflight.sh` lines 169 and 213: still use the caller account, but only on the same-account branch that P2 leaves unchanged.

## Upgrading the sample

Export the new upstream commit the same way, re-apply P1-P3 by searching for the `PATCH P<n>` markers, re-run the test suite, and update the tables above.
