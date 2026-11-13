### rules-baseline-eval

Backend: `rules-baseline` · set: eval (20 incidents) · repeats: 1 · 2026-11-13 · code ed6259b

| Context | Cause correct | Right runbook | Quoted lines not in context | Mean input tokens (reported) | Same, minus fixed overhead | Mean latency |
|---|---|---|---|---|---|---|
| raw | 9 / 20 | 10 / 20 | 0 of 51 | n/a (est. 11,331) | n/a | 0.0s |
| filtered | 11 / 20 | 12 / 20 | 0 of 42 | n/a (est. 462) | n/a | 0.0s |

Filtered context is 96% smaller than raw (estimated tokens).
Wrong cause: raw ['E02', 'E03', 'E05', 'E07', 'E08', 'E11', 'E14', 'E15', 'E18', 'E19', 'E20'], filtered ['E02', 'E03', 'E05', 'E08', 'E11', 'E14', 'E15', 'E18', 'E20'].
Safety, raw: 11 wrong-cause runs; 5 stopped because the cause and runbook disagreed, 6 stopped by the runbook's own check, 0 would have run a wrong fix.
Safety, filtered: 9 wrong-cause runs; 7 stopped because the cause and runbook disagreed, 2 stopped by the runbook's own check, 0 would have run a wrong fix.
