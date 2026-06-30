# Enterprise Batch Slow Gates

`scripts/enterprise_batch_slow_gates.py` produces a formal JSON artifact for the heavy enterprise batch checks that are too expensive for the normal unit-test loop.

## What It Proves

- Generated `batch_1500` pipeline: upload records, preflight, parse, grouping, batch layout, Top3 plan scoring, hard-rule rate, quantity fulfillment, and legal Top3 rate.
- Generated `batch_20000` pipeline: the same generated SVG/DXF/PDF-placeholder pipeline at the larger file-count target.
- Real sample classification fixture: the accepted real customer sample filenames exist locally when required, and the accepted bbox fixture still classifies as `FULL_SHEET`, `ANCHOR`, `FILLER`, or `OVERSIZE`.
- Dataset labels are explicit: generated 1500/20000 evidence is synthetic generated artwork evidence; real sample evidence is fixture bbox classification evidence.

## What It Does Not Prove

- It does not prove native PDF die-line extraction.
- It does not prove final production placement coordinates for PDF/AI/CDR samples.
- It does not prove configured PackingSolver/Sparrow binaries unless those are separately configured and accepted in solver-governance evidence.

## Full Acceptance Command

Run this on a workstation that has the real sample directory:

```powershell
python scripts\enterprise_batch_slow_gates.py `
  --output artifacts\enterprise-batch-slow-gates.json `
  --batch-1500-count 1500 `
  --batch-20000-count 20000 `
  --real-sample-root "D:\大卖数智AI部\包装印刷\甘-包装样例" `
  --hash-real-sample-files
```

The command exits nonzero when any gate fails. The report contains `thresholds`, `dataset_labels`, `coverage`, `summary`, and per-gate details.

## Preflight Integration

The normal `release_preflight.py` stays fast by default. Add the slow gates explicitly when preparing release evidence:

```powershell
python scripts\release_preflight.py `
  --include-slow-batch-gates `
  --real-sample-root "D:\大卖数智AI部\包装印刷\甘-包装样例" `
  --hash-real-sample-files `
  --report-path artifacts\release-preflight.json `
  --evidence-output-dir artifacts\release-preflight-evidence
```

For development-only smoke checks without the real sample directory:

```powershell
python scripts\enterprise_batch_slow_gates.py `
  --output tmp\enterprise-batch-slow-gates-dev.json `
  --batch-1500-count 25 `
  --batch-20000-count 30 `
  --allow-missing-real-samples
```

## Offline Verification

`scripts/verify_release_preflight.py` validates the slow gate payload whenever `include_slow_batch_gates=true` in a preflight report. It checks status, dataset labels, coverage for `batch_1500`, `batch_20000`, real-sample classification, 787x1092, MOQ1000, Top3, and synthetic labeling.

## Current Local Full Evidence

The full local gate has been run against the real sample directory with file hashing:

```text
report=artifacts\enterprise-batch-slow-gates-full.json
report_status=passed
synthetic_file_count=21500
real_sample_case_count=6
failed_gate_count=0
error_count=0
coverage=batch_1500,batch_20000,real_sample_directory,sheet_787x1092,moq_1000,top3
```

The generated benchmark pipeline uses bulk persistence and bulk parsing inside `EnterpriseBenchmarkRunner.run_batch_pipeline()` so the 1500/20000 slow gates are practical release artifacts without changing the normal user upload/parse transaction behavior.
