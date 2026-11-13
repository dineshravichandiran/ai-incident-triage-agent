### llm-small-eval

Backend: `command` · set: eval (20 incidents) · repeats: 2 · 2026-11-13 · code ed6259b

| Context | Cause correct | Right runbook | Quoted lines not in context | Mean input tokens (reported) | Same, minus fixed overhead | Mean latency |
|---|---|---|---|---|---|---|
| raw | 37 / 40 | 38 / 40 | 0 of 120 | 20,402 | 18,654 | 23.78s |
| filtered | 39 / 40 | 40 / 40 | 4 of 120 | 2,418 | 670 | 12.81s |

Filtered input is 88% fewer tokens than raw as reported, 96% fewer once the fixed overhead (1,748 tokens per call) is taken out.
Wrong cause: raw ['E03', 'E11'], filtered ['E03'].
Safety, raw: 3 wrong-cause runs; 2 stopped because the cause and runbook disagreed, 1 stopped by the runbook's own check, 0 would have run a wrong fix.
Safety, filtered: 1 wrong-cause runs; 1 stopped because the cause and runbook disagreed, 0 stopped by the runbook's own check, 0 would have run a wrong fix.
