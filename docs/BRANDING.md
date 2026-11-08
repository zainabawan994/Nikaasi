# Nikaasi — Brand Identity

## Name
**Nikaasi** (نکاسی), a twist on *Nikaas*. In Urdu, *nikaasi* is the everyday word for **drainage / outflow**, as in *nikaasi-e-aab* (water drainage). The system gets water, traffic and people *out* of harm's way.

## Tagline
**"Pani se pehle, plan."** (*Plan before the water.*)
Public headline: **"Pani bhar gaya? Batayein."**

## Design direction: operations dashboard
The staff app follows an **emergency-operations dashboard** layout:
- a **navy sidebar** for navigation: Dashboard, Incidents, Map view, Dispatches, Alerts, Feeds & weather, Settings, and Users for admins
- a light workspace with white cards
- a **dark live flood map** where problem roads glow and normal roads stay quiet
- on the dashboard: four KPI cards (active incidents, rain, traffic, system status) with sparklines; an incident card with a **confidence ring** and an **AI reasoning checklist**; a **response-actions stepper**; **before/after** mini maps; and **live feeds**

**Rules**
- No emojis. Icons are thin **line icons** (24 px grid, `currentColor`) from `public/js/icons.js`. Map markers are lettered tiles: **D** drain team, **R** rescue, **P** police.
- Rounded cards (12 px), soft shadows, pill badges for severity and status.
- Public pages use plain severity words (*Normal, Watch out, Flooding, Danger*). Staff pages use the system terms (*Watch, Moderate, High, Critical*).

## Logo
`public/assets/logo.svg`: a blue raindrop (light to deep blue) with two white wave lines. It sits on the navy sidebar or top bar, or on white. Minimum size 24 px.

## Colour

| Token | Hex | Use |
|---|---|---|
| Navy | `#0D1F3C` | Sidebar, public top bar and hero |
| Workspace | `#F1F4F9` | Page background |
| Card | `#FFFFFF` | Panels |
| Ink | `#0F172A` | Text |
| Muted | `#64748B` | Secondary text |
| Blue | `#2563EB` | Primary actions, active nav, links |
| Map night | `#111B2B` | Dark map background |
| **Severity** | | |
| Watch / Normal | `#64748B` (staff) · `#22C55E` on maps | |
| Moderate | `#CA8A04` · `#EAB308` on maps | |
| High | `#EA580C` · `#F97316` on maps | |
| Critical / Severe | `#DC2626` · `#EF4444` on maps | |
| OK | `#16A34A` | Resolved, operational |

## Typography
- **Plus Jakarta Sans** (400–800): all interface text and numbers.
- **JetBrains Mono**: incident and ticket IDs, timestamps.

## Voice
Calm, specific, bilingual. Every alert is sent in **English and Roman Urdu** and names the road, the action and the emergency number:
> [Nikaasi CRITICAL] Flooding in G-10. Srinagar Hwy — G-10 Underpass closed — use alternate routes. Emergency: 1122.
> [Nikaasi] G-10 mein pani bhar gaya hai. … band hai, doosra rasta lein.
