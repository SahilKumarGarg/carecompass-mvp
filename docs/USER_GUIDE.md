# CareCompass — User Guide

> **Demo data only. This app does not provide medical advice. All services, phone
> numbers, and addresses are entirely synthetic.**

---

## What CareCompass does

CareCompass helps you find the right local healthcare service for a concern you describe
in plain language. It:

- Checks whether your description contains signs of an emergency or urgent situation.
- Finds matching local services from a demo directory of 20 synthetic services.
- Shows you the best option with its phone number, opening hours, distance, and the date
  the service record was last verified.
- Offers a short plain-text summary you can share with a healthcare professional — but
  only if you ask for it.

**It does not diagnose. It does not suggest medicines. It never says what condition you
have.**

---

## Starting the app

```powershell
streamlit run app.py
```

Open your browser to `http://localhost:8501`.

---

## Step-by-step walkthrough

### 1. Pick your area

In the sidebar, select the demo area that represents your location:

| Area | What is available |
|---|---|
| **Greenfield** | Pharmacy, GP practice, urgent treatment centre, diabetes clinic, telemedicine |
| **Riverside** | 24-hour pharmacy, medical centre, A&E, cardiology outpatients, video GP |
| **Northgate** | Late-night pharmacy, health hub, walk-in centre, mental health team, online clinic |
| **Eastfield** | Pharmacy, GP surgery, 24-hour A&E (with paediatric unit) |
| **Westbury** | Pharmacy, primary care network (extended Friday hours) |

### 2. Describe your concern

Type your concern in the chat box at the bottom. Plain language is fine — full sentences,
fragments, or a mix:

> *"My mum's face is drooping on one side and she can't speak properly"*
>
> *"I need to order a repeat prescription for my blood pressure tablets"*
>
> *"My 8-month-old has had a fever of 39 degrees since this morning"*

Press **Enter** or click the send arrow.

### 3. Read the tier badge

The coloured banner at the top tells you how urgent the app considers the situation:

| Badge | Colour | Meaning |
|---|---|---|
| 🔴 **Emergency** | Red | Signs of a potentially life-threatening emergency were detected. Call the emergency number immediately. |
| 🟠 **Urgent** | Amber | The situation needs attention today. Contact an urgent care service within a few hours. |
| 🟢 **Routine** | Green | No urgent red flags detected. A routine appointment or pharmacy visit is likely suitable. |

The **Why** line under the badge names the red-flag rule that fired (e.g. "stroke signs",
"self harm thoughts", "fever in child").

### 4. Emergency response

If the tier is **Emergency**, the screen shows the emergency message:

> *"This is an emergency. These symptoms need emergency help right now. Please call
> [DEMO-999] immediately or ask someone nearby to call for you. Do not drive yourself.
> Stay on the line with the operator — they will tell you what to do while help is on
> the way."*

No service search is performed. The app skips to offering a handoff summary (see step 7).

### 5. Service recommendation (urgent and routine)

For urgent and routine sessions, the top recommendation is displayed as a card:

```
Eastfield Hospital A&E
─────────────────────────────────────────────────
Type:    Hospital / Emergency      Area:    Eastfield
Phone:   [DEMO-0403]               Hours:   Mon-Sun 00:00-24:00
Distance: ~0.8 km (~2 min est.)    Last verified: 2026-10-15

Services include: emergency care, paediatric A&E, acute medicine.
Data last verified: 2026-10-15.
```

Below the main card, click **All options** to see the full ranked list including the
fallback option and composite scores.

**What the score means**

Each service is scored 0–1 on four factors:

| Factor | Routine weight | Urgent weight |
|---|---|---|
| Type fit (how well the service type matches your concern) | 40 % | 25 % |
| Distance (closer = higher) | 20 % | 35 % |
| 24-hour availability | 20 % | 30 % |
| Data freshness | 20 % | 10 % |

Urgent sessions weight proximity and 24-hour access more heavily because time matters more.

**Stale data warning**

If a service record was last verified more than 60 days ago, the reason line says:
*"Last verified N days ago — call ahead to confirm details are still current."*

### 6. Needs-human-review notice

If the recommendation cannot pass all internal quality checks after two attempts, the app
shows:

- For **urgent/emergency**: a `[URGENT]` or `[EMERGENCY]` banner, the rule that fired,
  instructions to call the emergency number or go to A&E immediately, and a handoff offer.
- For **routine**: *"We could not confirm a match."* with instructions to call your GP or
  the emergency number if worried.

### 7. Handoff summary (optional, consent-gated)

After a recommendation is shown, the app asks:

> *"Would you like a summary of this conversation to share with a healthcare professional?"*

- Click **✅ Yes** to generate a short plain-text summary containing: who the concern is
  about, the main concern, how long it has been going on, the urgency tier, and the top
  service suggested. The summary is shown in the chat and logged.
- Click **❌ No** to decline — nothing is created.

**The summary is never created automatically.** Clicking Yes is the only trigger.

### 8. Starting a new session

Click **🔄 Start over** in the sidebar at any time to clear the chat and reset all state.

---

## What the app will never do

- **Name a diagnosis.** Phrases like "you probably have", "you may have", "this is a case
  of", or "your diagnosis is" are permanently blocked. If the system ever produces one, the
  `final_check` stage catches it before it reaches you.
- **Prescribe or dose medication.** Phrases like "take 500 mg", "dosage of", "take
  ibuprofen", "take paracetamol", or "I recommend taking" are blocked the same way.
- **Name serious conditions.** Words like "appendicitis", "sepsis", "meningitis", or
  "myocardial infarction" in the answer text are blocked.
- **Invent service details.** Every phone number, opening hour, and last-verified date
  in an answer must exist verbatim in `data/services.json`. The `final_check` stage
  verifies this before the answer is shown.

---

## Frequently asked questions

**Why do I need to pick an area?**  
The app filters services by area so it can estimate distance and find the most relevant
local option. If you pick the wrong area, the services shown will be for that area.

**What does "last verified" mean?**  
It is the date the demo service record was last checked. In a real deployment this would
reflect when a person last confirmed the service's details. The app shows it so you know
how current the information is.

**Can I use this in a real emergency?**  
No. This is a demonstration application with entirely synthetic data. In a real emergency,
call your national emergency number immediately.

**Does the app store anything about me?**  
The app writes a structured log to `logs/audit.jsonl` on the machine where it is running.
The log records which steps ran, what tier was assigned, and which services were suggested.
It does not record the full text of your message in production — only the step decisions.
No data leaves the local machine unless you configure the optional watsonx.ai integration.

**What if the app says it could not find a match?**  
This means the final quality check failed twice. The app will either show urgent escalation
instructions (if the tier was urgent or emergency) or suggest calling your GP directly.
