# Customer config (`customers/<slug>/config.json`)

Copy `customers/_template/` to `customers/<slug>/`. Everything under `customers/` except `_template/` is gitignored, and `tools/scan_staged.py` reads `slug` and `display_name` from every filled config to block them from commits.

| Field | Example shape | Rule |
|---|---|---|
| `slug` | `acme` | Lowercase letters, digits and hyphens, starts with a letter, at most 15 characters. The sample's `STACK_PREFIX` is `kiro-<slug>` and is limited to 20 characters. |
| `display_name` | `ACME Retail` | Shown on dashboards. Also blocked from commits. |
| `accounts.payer` | 12 digits | Account that owns the CUR 2.0 export (management or payer account). |
| `accounts.kiro` | 12 digits | Account whose Kiro profile has the subscribed users. The report bucket and the prompt-log bucket live here. |
| `accounts.identity_center` | 12 digits | Account of the IAM Identity Center instance (management or delegated admin). |
| `regions.kiro_profile` | `us-east-1` | Region of the Kiro profile. The report bucket must be in this region. |
| `regions.identity_center` | `ap-southeast-1` | Region of the Identity Center instance. |
| `report_bucket` | bucket name | Bucket set as the user activity report location in the Kiro console. SSE-S3 only. |
| `report_prefix` | `kiro/` | Must stay `kiro/`: the CID Data Collection Stack does not pass another prefix to its Kiro module (design.md 3.2). |
| `identity_store_id` | `d-` and 10 characters | Identity Store ID, not the `ssoins-` instance ID. Filled by the survey. |
| `external_id` | `kiro-mon-<slug>-<8 random>` | One per customer. Not a secret, but it must be unique. |
| `profiles.*` | CLI profile names | Local profile names used by `tools/survey_customer.py`. Each profile must resolve to the matching account, or the survey stops. |
