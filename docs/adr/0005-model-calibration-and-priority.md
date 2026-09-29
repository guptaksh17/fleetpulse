# ADR 0005: Calibrated per-component models and priority by expected loss

**Status:** accepted (Phase 5-6)

## Context
Fleet managers need a ranked list of what to service, not raw scores. Rare events (2-3 percent
positive) make accuracy meaningless, and rankings must be comparable across components.

## Decision
- **Labels:** an event in (t, t + 7 d]. Snapshots taken while a component awaits service, and
  snapshots whose 7-day horizon runs past the end of the data, are excluded.
- **Splits:** chronological by prediction time, with buffers between them; never random. A
  20 percent vehicle holdout gives a secondary evaluation.
- **Models:** one per component. Logistic regression is the baseline, compared with
  HistGradientBoosting; both are class weighted.
- **Calibration:** Platt scaling fitted on the calibration window only. The alert threshold is
  chosen on a separate window, and the test window is used once.
- **Metrics:** PR-AUC is the headline; recall, precision, Brier and top-K are also reported.
- **Priority:** expected loss = P7d x (direct failure cost + downtime hours x cost per hour).
  Missing cost data gives COST_DATA_REQUIRED, never zero.

## Consequences
- Probabilities are meaningful, so expected losses add up across the fleet.
- **BRAKE is weakly predictable** (test PR-AUC 0.108 against a prevalence of 0.032). The
  simulator's brake observables only change during harsh braking, which is rare. This is reported
  rather than hidden.
- The served model is picked by PR-AUC on the threshold split, so the test split is only used for
  the final report.
