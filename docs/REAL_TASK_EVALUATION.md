# Real task delivery evaluation

These are development measurements, not an industrial SLA or a comparison with other agents. Passing native commands proves execution against an agent-declared coverage plan; independent task oracles separately check functionality.

## Frozen candidate 0a8bed6

Conditions: DeepSeek Flash provider alias (exact snapshot unknown), max reasoning, unchanged full output cap, $1 and 1200 active seconds shared across each task's original turns, 40 model calls per native turn. Three tasks with two fresh repetitions were planned. Private graders were inaccessible to the target.

| Executed trial | Independent functionality | Final native proof | Complete original turns | Accepted delivery |
|---|---|---|---|---|
| Pocket Ledger 1 | 28/28 cases | verified | yes | yes |
| Durable Job Queue 1 | 8/8 cases | unverified | yes | no |
| Incremental cache 1 | C64/70; not accepted | no final | no; R3 timeout | no |

Observed accepted delivery was 1/3 executed trials. The remaining three planned repeats were **unexecuted** after unknown usage stopped the batch. This is not a six-trial success rate. All 994 frozen source files matched before/after. Protected original tests/data/documentation and read-only round snapshots remained unchanged.

Ledger R1/R2 and Queue R1/R3 attempted native binding but local intent recognition rejected natural work instructions. Ledger recovered in R3; Queue's final check remained unverified despite functional success. Regression tests now cover natural handoffs and qualified contract instructions, as well as questions, explanations, quoted instructions, prohibitions and historical completion reports.

The cache task spent 738.3 seconds in two read-only turns, including one response with 82,572 output tokens (reasoning included), then exhausted the shared 1200-second limit during R3. This does not establish that a larger timeout alone fixes the task.

Known usage receipts estimated $0.858529560. Native accounting additionally booked $0.330491100 for a request with unknown usage; another $0.193362352 was held to reserve the affected trial's full remainder. Including earlier conservative accounting, the cumulative amount reserved/accounted is $3.595813586. Unknown usage is not confirmed spending; provider billing has not been reconciled.

## Frozen evaluation 601bdd9

Before new requests, freeze the corrected source and retain the original fixtures, prompts, oracles, model, output policy and repeat count. Every trial receives $1 / 2400 active seconds. The deadline change is explicit, so outcome differences cannot be attributed solely to the code fix. Unknown usage stops its trial without retry and holds its entire allocation; remaining independent trials have separate prepaid allocations. Six new allocations bound cumulative planned accounting at $9.595813586 within the $10 authorization.

All six trials completed observation. Independent functionality passed 5/6; accepted delivery passed 0/6. Native verdicts are retained unchanged. File-specific evidence was rejected by the frozen evaluator; later coverage fixes have only diagnostic replay/regression evidence and have not completed equivalent live repeated evaluation. Trial 3 stopped at its active deadline; trial 4 ended without completing the requested turns; trial 6 changed protected original files. Read-only snapshots remained unchanged in all six. These results do not establish reliability of the newer release.

| Trial | Independent functionality | Protected originals | Complete original turns | Final native verified | Accepted delivery |
|---|---|---|---|---|---|
| 1 ledger 1 | 28/28 | yes | yes | no | no |
| 2 queue 1 | 8/8 | yes | yes | no | no |
| 3 incache 1 | C70/70 | yes | no | no | no |
| 4 ledger 2 | 3/28 | yes | no | no | no |
| 5 queue 2 | 8/8 | yes | yes | no | no |
| 6 incache 2 | C70/70 | no | no | no | no |

Known new usage receipts total $2.101004700; all six trial usage records are complete. Including earlier conservative accounting of $3.595813586, cumulative known/reserved accounting is $5.696818286; earlier unknown usage remains a reserve rather than a confirmed bill.

Stable-release evidence remains: all original turns complete, independent functional acceptance, current native final proof, unchanged protected fixtures, read-only compliance, passing release checks, and a verified installed macOS application built from the published commit. A v1.3.0 preview is published at the user's explicit request with these limitations disclosed; it is not a stable-delivery certification. An installer build alone does not satisfy the stable gates.
