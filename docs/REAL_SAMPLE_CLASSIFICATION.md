# Real Sample Classification Fixtures

This document records the target real-sample classification evidence for the packaging sample directory:

```text
D:\大卖数智AI部\包装印刷\甘-包装样例
```

## Fixture

The portable fixture lives at:

```text
samples/artworks/real-sample-classification-fixtures.json
```

It covers the enterprise target classes from the audit prompt:

| Case | Real sample filename | Expected class |
| --- | --- | --- |
| `coffee_machine_full_sheet` | `doyomi咖啡机栅格化（蓝）335x189x365mm .pdf` | `FULL_SHEET` |
| `soy_milk_machine_anchor` | `DOYOMI豆浆破壁机终（栅格化）.pdf` | `ANCHOR` |
| `large_outer_box_anchor` | `M20T_外包装盒_V5(3).pdf` | `ANCHOR` |
| `gage_box_filler` | `gage包装外盒.pdf` | `FILLER` |
| `capsule_box_filler` | `胶囊盒2栅格化.pdf` | `FILLER` |
| `cat_litter_box_oversize` | `猫砂盆包装设计稿.pdf` | `OVERSIZE` |

The fixture stores representative accepted bounding boxes and expected classes. These values are not native PDF production coordinates.

## Audit Command

Run this on a workstation that has the real sample directory:

```powershell
python scripts\audit_real_sample_classification.py --require-files --output tmp\real-sample-classification-audit.json
```

Optional file hashing:

```powershell
python scripts\audit_real_sample_classification.py --require-files --hash-files --output tmp\real-sample-classification-audit.json
```

The enterprise slow-gate artifact can include this same real-sample fixture alongside generated 1500/20000 batch evidence:

```powershell
python scripts\enterprise_batch_slow_gates.py --output artifacts\enterprise-batch-slow-gates.json --real-sample-root "D:\大卖数智AI部\包装印刷\甘-包装样例" --hash-real-sample-files
```

## Current Local Evidence

The latest local run found all six real sample files and all six expected classifications matched.

```text
report_status=passed
case_count=6
classification_match_count=6
missing_file_count=0
error_count=0
```

The reduced enterprise slow-gate smoke with `--hash-real-sample-files` also passed against the same directory:

```text
report=tmp\enterprise-batch-slow-gates-reduced-real-samples-hash.json
synthetic_file_count=27
real_sample_case_count=6
error_count=0
```

The full enterprise slow gate also passed:

```text
report=artifacts\enterprise-batch-slow-gates-full.json
synthetic_file_count=21500
real_sample_case_count=6
error_count=0
```

## Boundary

- PDF/AI/CDR files remain conversion/manual-review inputs until a tested native parser or accepted conversion supplier path is available.
- The classification fixture proves the enterprise class decision rules for these real sample names and accepted dimensions.
- The enterprise slow gate labels this evidence as `real_customer_sample_fixture_bbox`.
- It does not prove exact production placement geometry or native PDF die-line extraction.
