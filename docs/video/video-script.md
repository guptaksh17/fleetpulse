# FleetPulse demo video: 5-minute script

**Time budget:**

| Part | Time | Share |
|---|---|---|
| Intro, problem and solution (slides) | 1:30 | 30% |
| Live product demo | 2:20 | 47% |
| Results, impact and close (slides) | 1:10 | 23% |

The narration is written word for word at about 140 words per minute. Read it aloud once with a
timer; if you run long, cut the lines marked *(optional)*.

---

## Before you record (15 minutes)

**Stack and data:**
1. Start Docker Desktop, then start the stack:
   ```bash
   cd ~/Desktop/fleetPulse && docker compose start kafka postgres timescaledb redis identity-resolver normalization stream-processor rule-engine scorer api web
   ```
2. Check the stack: `docker compose ps` shows everything Up, and http://localhost:3000 loads.
3. Do one warm-up run of the live script so Kafka and the caches are warm. It takes about 2 min:
   ```bash
   SMOKE_SKIP_BUILD=1 SMOKE_WALL_SECONDS=30 bash scripts/smoke_live.sh
   ```
   It must end with `SMOKE TEST PASSED`. If Kafka shows as unhealthy, run `docker restart kafka`
   and wait 30 s.
4. Ask the assistant one question off camera. The footer should say `llm:openai/gpt-oss-120b`.

**Screen setup (Chrome, full screen, bookmarks bar hidden with ⌘⇧B, zoom 110%):**

| Tab | Content |
|---|---|
| 1 | `docs/video/slides.html`: open it from Finder, press **F** for full screen, then **Esc** so it is a normal tab |
| 2 | http://localhost:3000, logged in as **manager** (on Pulse) |
| 3 | http://localhost:3000, logged in as **viewer**. Type the URL in a new tab; each tab has its own login. |

- **Terminal:** large font (⌘+ a few times), in `~/Desktop/fleetPulse`, with this command typed
  but **not** run yet:
  ```bash
  SMOKE_SKIP_BUILD=1 SMOKE_WALL_SECONDS=30 bash scripts/smoke_live.sh
  ```
- **Mac:** Do Not Disturb on. Close Slack, Mail and WhatsApp. Hide the Dock (⌥⌘D).

**Recording:**
- Use ⌘⇧5 → *Record Entire Screen* → *Options* → your microphone.
- Or use QuickTime → *File* → *New Screen Recording*.
- Easiest: record three takes and join them in QuickTime (*Edit* → *Add Clip to End*) or
  iMovie:
  - **Take A:** slides 1-3
  - **Take B:** the live demo
  - **Take C:** slides 4-5

  A mistake then only costs one take.

---

## Take A: intro, problem and solution (0:00-1:30), slides

### 0:00-0:20 · Slide 1: title
> Hi, we're [team name]. This is **FleetPulse**. It predicts which brake, powertrain or battery in
> a fleet will need service in the next seven days, puts a dollar value on that risk, and tells
> the fleet manager exactly what to fix first.

### 0:20-1:00 · Slide 2: the problem (→)
> Today, most fleets find out about a failure when the truck stops on the road. That means a tow,
> an emergency repair and a lost day of deliveries.
>
> The usual fix is calendar-based maintenance. But that sends healthy vehicles to the workshop
> and misses the ones that are actually failing.
>
> And workshops have a fixed number of slots. So the real question a fleet manager has every
> Monday is not "what could go wrong". It's "**which vehicles should I fix this week to avoid
> the most expensive breakdowns?**" That's the question FleetPulse answers.

### 1:00-1:30 · Slide 3: how it works (→)
> We simulated **100,000 connected vehicles** from two different manufacturers, with hidden wear
> that leads to real failures.
>
> Their telemetry flows through Kafka. We validate every VIN and quarantine bad messages. An
> adapter per manufacturer converts everything into one format.
>
> A stream processor removes duplicates and keeps exact 5-minute, 1-hour and 24-hour windows.
> Calibrated models turn those windows into a 7-day failure probability, multiplied by repair
> and downtime cost.
>
> Everything is served through a secure, multi-tenant API to a dashboard and an AI assistant.
> Let me show you.

---

## Take B: live demo (1:30-3:50)

### 1:30-1:40 · Terminal: start the live stream
Press **Enter** on the prepared command, then switch straight to tab 2 (Pulse).
> I've just started 50 live vehicles streaming in, and one of them is about to develop a
> critical brake fault. We'll come back to that.

### 1:40-2:00 · Pulse
Point at the cards, then the risk chart.
> This is Alex's view: a fleet manager with fifty thousand vehicles. A hundred and twenty-five
> thousand components are scored live. Five hundred and thirty-six are above their risk
> threshold, and the expected loss over the next seven days is **twelve point three million
> dollars**.

### 2:00-2:15 · Priority
Click **Priority**, then click **BATTERY**, then **POWERTRAIN**.
> The queue is ranked by **dollars at risk**, not raw probability. An EV battery at sixty
> percent risk outranks a brake at ninety percent, because a battery costs about eleven
> thousand dollars and a brake about three.

### 2:15-2:35 · Vehicle detail
Click the top POWERTRAIN VIN (a diesel van).
> Here's why this van is flagged: ninety-five percent powertrain risk against a forty percent
> threshold, about ten thousand dollars at stake, and you can see its engine running hot.
> *(optional)* Battery shows n/a, because we never score a part a vehicle doesn't have.

### 2:35-2:55 · Live alert
The toast pops up about 60 s after launch: **DTC_BRAKE_SYSTEM_CRITICAL on 1HGCM8267MA500000**,
with **Open** and **Acknowledge** buttons. It stays on screen for 20 s. Click **Acknowledge** on
the toast. The Alerts page and the *Live alerts* panel on Pulse (**Ack**) work too. A few
powertrain risk toasts may follow; that is the scorer re-scoring the live vehicles.
> And there it is: the brake fault from the live stream, raised as an alert within seconds by a
> rule engine that doesn't wait for any ML. Alex acknowledges it right from the notification,
> so the team doesn't handle it twice. Twenty percent of those messages were deliberate
> duplicates, and every one was dropped.

If the toast is late, keep talking on the Alerts page. You have until about 2 minutes after
launch before the script clears the alert.

### 2:55-3:20 · Assistant
Click **Assistant**, then the suggestion *"Which batteries are most at risk and what will they
cost me?"*.
> Alex can also just ask. The language model can't touch the database. It can only call four
> read-only tools, scoped to Alex's own company, and every question is audited. You can see
> which tool it used and how long it took.

### 3:20-3:35 · Viewer and tenant isolation
Switch to tab 3 (viewer), show Pulse, then open Alerts.
> This is a different customer on the same platform. Different fleet, different numbers, and a
> read-only role: no acknowledge button. Alex's vehicles simply don't exist for them. Tenant
> isolation is enforced in every database query, not just the UI.

### 3:35-3:50 · Models
Back in tab 2, click **Models**.
> Can you trust the predictions? Failures are rare, so we measure PR-AUC on a later time window
> the model never saw. Powertrain is **twenty-one times better than chance**. Brake is weak,
> and we show that honestly instead of hiding it.

---

## Take C: results and close (3:50-5:00), slides

### 3:50-4:30 · Slide 4: results
> Every number here is measured, with evidence in the repository:
> - One hundred thousand vehicles, and seven point six million telemetry rows.
> - One point seven seconds from a sensor reading to an updated risk score.
> - An API p95 of a hundred and sixteen milliseconds, against a two-hundred target.
> - We killed the Kafka broker mid-stream for thirty-nine seconds: zero duplicates, and it
>   recovered on its own.
> - Ninety-six percent of the powertrains we flag in the top one percent really do need
>   service.
> - Streaming and offline features match exactly, and ninety-five automated tests plus security
>   scans run in CI.

### 4:30-5:00 · Slide 5: close (→)
> So, FleetPulse gives every component a calibrated seven-day risk in dollars, raises critical
> alerts in seconds, and keeps each customer's data private. It runs locally with Docker and
> deploys to AWS or GCP with Terraform and Kubernetes. Next, we'd scale the stream processor to
> a hundred thousand events per second and pilot it on real OEM data. Thank you.

---

## Checklist after recording
- [ ] The total is 5:00 or less. Trim silences at the start and end.
- [ ] Voice is audible. Add captions (iMovie → *Titles*, or YouTube auto-captions).
- [ ] No passwords or API keys visible on screen. The terminal shows only the smoke command
      output.
- [ ] Upload as an unlisted YouTube or Google Drive link, and paste it into the Solution
      Document.
