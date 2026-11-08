# Demo script (Checkpoint 4) — about 4 minutes

**Setup:** `npm start`, then open two windows: **Authority** `http://localhost:3000/authority.html` (PIN `1122`) and **Citizen** `http://localhost:3000/citizen.html` (phone-size window, sector G-10).

| Clock | What happens | What to say |
|---|---|---|
| — | Press **▶ Play "G-10 monsoon" story** | "18:40 on a monsoon evening in Islamabad. Three systems that normally never talk to each other are feeding Nikaasi." |
| 18:42 | PMD orange alert appears in the *Signal feeds* table | "Weather knows heavy rain is coming. One signal alone isn't an incident." |
| 18:44 | Sana in **F-8** posts "thora sa pani hai" | "A single report with no corroboration stays on *Watch*. This isn't a complaint app that panics on every post." |
| 18:45 | Imran in **G-10**: "Pani bhar gaya hai… gaari phans gayi hai". Incident **NKS-0001** opens (Moderate) | Point at the explanation: report + orange alert, *Medium* confidence. |
| 18:46–18:51 | Traffic spikes on Srinagar Hwy, more Roman Urdu reports, then Hamza: "Motorcyclist gir gaya". **Critical, High confidence** | Read the explanation aloud. Show the stacked score bar and the hazard bonus. Open the *Reasoning trail*. |
| — | Show **Response plan v2**: close 4 segments, drain team with pump, Rescue 1122, alert 6,755 residents. Predicted impact tiles | "The AI plans from what it can observe. It never sees the true water depth." |
| — | Click **Approve & execute plan** | Roads turn into dashed closures. Avg trip time roughly halves and vehicles entering floodwater drop ~97 %. Tickets go Created → Assigned → En route, and you can watch the truck move on the map. |
| — | Switch to the **citizen window** | The bilingual warning has arrived in the inbox. The status card shows the closed roads. |
| — | Submit your own report from the citizen app (try a photo) | The AI's interpretation comes back, along with which incident it joined. |
| ~19:10 | Crew on site, pumping | *Response impact* panel: "Now" depth falls while "If nobody had acted" stays at 60 cm. |
| ~19:30 | Ticket resolved, roads reopen, all-clear alert sent, severity falls, incident **resolved** | "The severity fell because the fused signals changed, not because a timer ran out." |

Use the **speed** selector (4×) to fast-forward the drainage phase.

**Drill mode:** in *Signal feeds → Inject weather alert*, set any sector to red, then submit two citizen reports for it. A new incident opens without the script.
