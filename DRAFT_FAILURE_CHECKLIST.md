# Draft failure checklist

Read this before drafting, editing a saved draft or paying for AI quality checks.
Each curated row records a cause that stopped an earlier draft, check or run,
with how to avoid it. In this public showcase the curated tables are empty
examples; a real deployment fills them from its own run history.

## Curated causes

| What failed | Page type | How to avoid it |
| --- | --- | --- |
| Guide link missing in the core sections | blog | Link at least one existing guide page in S08 |
| Approved firm fact paraphrased | both | Copy facts verbatim from `inputs/brand-guidelines/facts.json` |
| Meta description over 155 characters | both | Count characters before saving |

## Automatically detected, not yet curated

With `FAILURE_CHECKLIST_AUTO=true`, the application appends each new kind of
recorded failure below. Review entries and move them into the table above.

<!-- AUTO-DETECTED:START -->
<!-- known-causes: [] -->
<!-- AUTO-DETECTED:END -->
