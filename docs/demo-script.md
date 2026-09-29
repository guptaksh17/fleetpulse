# Demo Video Script (5 minutes maximum)

The full, timed, click-by-click script is in [demo-workflow.md](demo-workflow.md), section 4. That file also contains the project workflow, the user story, the pre-demo checklist and a Q&A cheat sheet.

## Summary

| Time | Segment |
|---|---|
| 0:00-0:25 | Problem: fleets find out about failures when a truck stops |
| 0:25-1:00 | Pulse (manager login at http://localhost:3000): KPIs and the 7-day expected loss in $ |
| 1:00-1:30 | Priority: queue ranked by expected dollar loss |
| 1:30-2:00 | Vehicle detail: why it is flagged |
| 2:00-2:45 | Live: `SMOKE_WALL_SECONDS=30 bash scripts/smoke_live.sh`, then the DTC toast, Acknowledge, and the dedup invariant |
| 2:45-3:20 | Assistant: Groq tool-calling over tenant-scoped, read-only tools |
| 3:20-3:45 | Viewer login: tenant isolation (404 on another tenant's vehicle, 403 on actions) |
| 3:45-4:20 | Models: PR-AUC vs chance on a held-out later window |
| 4:20-4:45 | Pipeline: Kafka, dedup, and the chaos test result |
| 4:45-5:00 | Close |

## Recording tips
- Record at 1080p.
- Set browser zoom to 110-125%.
- Use a large terminal font.
- Add captions.
