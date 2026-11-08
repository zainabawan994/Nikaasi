# Nikaasi — Brand Identity

## Name
**Nikaasi** (نکاسی), a twist on *Nikaas*. In Urdu, *nikaasi* is the everyday word for **drainage / outflow**, as in *nikaasi-e-aab* (water drainage). The system gets water, traffic and people *out* of harm's way.

## Tagline
**"Pani se pehle, plan."** (*Plan before the water.*)
Public headline: **"Pani bhar gaya? Batayein."**

## Design direction: civic works
Nikaasi should look like something a municipality issues, not a generic app template. The references are:
- survey and engineering drawings: paper, grid lines, 1 px rules
- Pakistani road and traffic signage: condensed capitals, black and road-marking yellow
- emergency-operations boards: dense, ruled, numbers first

**Rules**
- No emojis or decorative icons. Labels are words: *Traffic*, *Drain*, *Rescue*, *Alert*. Map markers are lettered squares: **D** drain team, **R** rescue, **P** police.
- Square corners (3 px), 1 px borders, **no shadows, no colour gradients**. The only patterns are the 48 px survey grid on dark panels.
- Severity is shown with a solid colour bar or tag, never a soft glow.
- Public pages use plain words for severity (*Normal, Watch out, Flooding, Danger*). Staff pages use the system terms (*Watch, Moderate, High, Critical*).

## Logo
`public/assets/logo.svg`: a flat **river-blue raindrop** with a faint street grid, crossed by a **road-marking-yellow outflow arrow** (*nikaas*). No gradients. Use it on the ink top bar or on paper. Minimum size 24 px.

## Colour

| Token | Hex | Use |
|---|---|---|
| Ink | `#1E1D1A` | Text, top bar, dark panels |
| Survey paper | `#F2EEE6` | Page background |
| Sheet | `#FBF9F4` | Cards / panels |
| Tint | `#ECE6DA` | Recessed areas, empty bars |
| Rule | `#D8D1C3` | Borders, dividers |
| Margalla green | `#1F4E3D` | Primary actions (sign in, approve) |
| Road-marking yellow | `#E0A526` | Live state, attention, top-bar rule, public primary button |
| River blue | `#2F6690` | Water depth, citizen signal |
| **Severity** | | |
| Normal / Watch | `#8A8F84` | stone |
| Watch out / Moderate | `#D9A21B` | mustard |
| Flooding / High | `#C2571A` | burnt orange |
| Danger / Critical | `#A4161A` | brick red |
| Clear / OK | `#2E7D4F` | resolved, delivered |

Road congestion on the map runs from muted green `#4F7A5A` through mustard, burnt orange and brick to `#5E0B0E` (gridlock). Closed roads are dashed stone grey `#7A766C` with a brick-red square ✕.

## Typography
- **Barlow Condensed** (600/700, uppercase, slight letter-spacing): headings, buttons, labels, big numbers. This is the road-sign voice.
- **IBM Plex Sans**: body text and explanations.
- **IBM Plex Mono**: clocks, incident and ticket IDs, timestamps, rain readings.

## Voice
Calm, specific, bilingual. Every alert is sent in **English and Roman Urdu** and names the road, the action and the emergency number:
> [Nikaasi CRITICAL] Flooding in G-10. Srinagar Hwy — G-10 Underpass closed — use alternate routes. Emergency: 1122.
> [Nikaasi] G-10 mein pani bhar gaya hai. … band hai, doosra rasta lein.
