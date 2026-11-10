# CareCompass — User Guide

> **This app uses a real US hospitals dataset. Phone numbers and services may be out of
> date (records validated 2013–2022). The dataset has no emergency-department field and
> no opening-hours field — always call ahead before travelling.**
>
> **This app does not provide medical advice.**

---

## What CareCompass does

CareCompass helps you find a nearby hospital for a health concern you describe in plain
language. It:

- Checks whether your description contains signs of an emergency or urgent situation.
- Finds matching nearby hospitals from the US hospitals dataset filtered by the location
  you select.
- Shows you the best option with its phone number, distance estimate, and the year the
  record was last validated.
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

### 1. Set your location

Use the **📍 Set your location** panel to select where you are. The panel has four fields:

| Field | Notes |
|---|---|
| **State** (required) | Two-letter US state code. Required unless you enter a ZIP code. |
| **ZIP code** (optional) | 5-digit ZIP. Overrides county and city for the most specific match. |
| **County** (optional) | Narrows results within the selected state. |
| **City** (optional) | Narrows results within the selected state (and county, if chosen). |

The app resolves your location at the most specific level available and uses that as the
reference point for distance calculations. **If no hospitals are found at the most specific
level (e.g. a ZIP with no records), the app automatically widens to city, then county, then
state, and shows you a notice.**

A small caption below the panel confirms the active location level (e.g.
`Location: CHICAGO, COOK, IL`).

### 2. Describe your concern

Type your concern in the chat box at the bottom. Plain language is fine — full sentences,
fragments, or a mix:

> *"My mum's face is drooping on one side and she can't speak properly"*
>
> *"I need to find a hospital near me for a repeat prescription review"*
>
> *"My 8-month-old has had a fever since this morning"*

Press **Enter** or click the send arrow.

### 3. Read the tier badge

The coloured banner at the top tells you how urgent the app considers the situation:

| Badge | Colour | Meaning |
|---|---|---|
| 🔴 **Emergency** | Red | Signs of a potentially life-threatening emergency were detected. Call 911 immediately. |
| 🟠 **Urgent** | Amber | The situation needs attention today. Go to the nearest hospital without delay. |
| 🟢 **Routine** | Green | No urgent red flags detected. A routine hospital visit or call ahead is likely suitable. |

The **Why** line under the badge names the red-flag rule that fired (e.g. "stroke signs",
"self harm thoughts", "fever in child").

### 4. Emergency response

If the tier is **Emergency**, the screen shows the emergency message and a **Call 911 now**
button. No hospital search is performed. The app skips to offering a handoff summary
(see step 7).

### 5. Hospital recommendation (urgent and routine)

For urgent and routine sessions, the top recommendation is displayed as a card:

```
CHICAGO GENERAL HOSPITAL
─────────────────────────────────────────────────
Type:    General Acute Care      Location:  CHICAGO, IL
Phone:   (312) 555-0100           Verified:  2021
Distance: ~2.3 mi (~3 min est.)
```

Below the main card, click **All options** to see the full ranked list with composite
scores.

**What the score means**

Each hospital is scored on three dimensions:

| Dimension | Routine weight | Urgent weight |
|---|---|---|
| Type fit (how well the hospital type matches your concern) | 40 % | 40 % |
| Distance (closer = higher; max 10 mi scale) | 40 % | 45 % |
| Trauma centre (has designated trauma level) | 20 % | 15 % |

**Data currency warning**

Because records were validated between 2013 and 2022, the card shows only the validation
**year**, not a specific date. Always call the number shown to confirm the facility is still
open and appropriate for your situation.

### 6. Needs-human-review notice

If the recommendation cannot pass all internal quality checks after two attempts, the app
shows:

- For **urgent/emergency**: a `[URGENT]` or `[EMERGENCY]` banner, the rule that fired,
  instructions to call 911 or go to the nearest hospital, and a handoff offer.
- For **routine**: *"We could not confirm a match."* with instructions to call your doctor
  or 911 if worried.

### 7. Handoff summary (optional, consent-gated)

After a recommendation is shown, for **routine** sessions the app shows a button:

> **Create a summary to share with my provider**

Click it to generate a short plain-text summary containing: who the concern is about, the
main concern, how long it has been going on, the urgency tier, and the top hospital
suggested. The summary is shown in the chat.

**The summary is never created automatically.** Clicking the button is the only trigger.

For **urgent** sessions a **Get directions** button links to Google Maps. For **emergency**
sessions the button dials 911.

### 8. Starting a new session

Click **🔄 Start over** at the bottom of the page to clear the chat and reset all state.

---

## What the app will never do

- **Name a diagnosis.** Phrases like "you probably have", "you may have", "this is a case
  of", or "your diagnosis is" are permanently blocked. If the system ever produces one, the
  `final_check` stage catches it before it reaches you.
- **Prescribe or dose medication.** Phrases like "take 500 mg", "dosage of", "take
  ibuprofen", "take paracetamol", or "I recommend taking" are blocked the same way.
- **Name serious conditions.** Words like "appendicitis", "sepsis", "meningitis", or
  "myocardial infarction" in the answer text are blocked.
- **Invent hospital details.** Every phone number, address, and hospital name in an answer
  must exist verbatim in `data/hospitals_clean.csv`. The `final_check` stage verifies this
  before the answer is shown.

---

## Frequently asked questions

**Why do I need to pick a location?**
The app filters hospitals by your location so it can estimate distance and find the nearest
relevant option. If you select the wrong location, the hospitals shown will be for that
area. At minimum, select a state; add a city or ZIP for a more specific list.

**What does "Verified" year mean?**
It is the year the hospital record was last validated in the source dataset. Records range
from 2013 to 2022. The app shows it so you know how current the information is. Always
call the hospital to confirm details are still accurate.

**Can I use this in a real emergency?**
No. In a real emergency, call 911 immediately. This application may show stale or
incomplete data and must not be used as a substitute for calling emergency services.

**The dataset has no emergency-department field — how does the app choose hospitals?**
The app uses facility type as a proxy. For emergency and urgent tiers it prefers
`GENERAL ACUTE CARE` hospitals, then `CRITICAL ACCESS` hospitals, and promotes
trauma-designated facilities. This is a best-effort filter; not all recommended hospitals
will have an ED. Call ahead.

**Does the app store anything about me?**
The app writes a structured log to `logs/audit.jsonl` on the machine where it is running.
The log records which steps ran, what tier was assigned, and which hospitals were suggested.
It does not record the full text of your message — only the step decisions. No data leaves
the local machine unless you configure the optional watsonx.ai integration.

**What if the app says it could not find a match?**
This means the final quality check failed twice. The app will either show urgent escalation
instructions (if the tier was urgent or emergency) or suggest calling your doctor directly.
