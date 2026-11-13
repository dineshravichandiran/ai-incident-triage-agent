### rules-baseline-dev

Backend: `rules-baseline` · set: dev (10 incidents) · repeats: 1 · 2026-11-13 · code ed6259b

| Context | Cause correct | Right runbook | Quoted lines not in context | Mean input tokens (reported) | Same, minus fixed overhead | Mean latency |
|---|---|---|---|---|---|---|
| raw | 4 / 10 | 5 / 10 | 0 of 21 | n/a (est. 10,943) | n/a | 0.0s |
| filtered | 6 / 10 | 6 / 10 | 0 of 20 | n/a (est. 408) | n/a | 0.0s |

Filtered context is 96% smaller than raw (estimated tokens).
Wrong cause: raw ['D02', 'D03', 'D05', 'D06', 'D07', 'D08'], filtered ['D02', 'D05', 'D06', 'D08'].
Safety, raw: 6 wrong-cause runs; 4 stopped because the cause and runbook disagreed, 2 stopped by the runbook's own check, 0 would have run a wrong fix.
Safety, filtered: 4 wrong-cause runs; 3 stopped because the cause and runbook disagreed, 1 stopped by the runbook's own check, 0 would have run a wrong fix.
