'use strict';
/**
 * Google AI Studio (Gemini API) adapter.
 * Set GEMINI_API_KEY (from https://aistudio.google.com/apikey) to enable.
 * Every call has a timeout and falls back to the offline engines, so the demo
 * never breaks on bad Wi-Fi.
 */

const API = 'https://generativelanguage.googleapis.com/v1beta/models';

function enabled() { return Boolean(process.env.GEMINI_API_KEY); }
function model() { return process.env.GEMINI_MODEL || 'gemini-3.5-flash'; }

async function generateJSON(parts, { timeoutMs = 20000, temperature = 0.2 } = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    let res;
    for (let attempt = 0; attempt < 2; attempt++) {
    res = await fetch(`${API}/${model()}:generateContent`, {
      method: 'POST',
      signal: ctrl.signal,
      headers: { 'content-type': 'application/json', 'x-goog-api-key': process.env.GEMINI_API_KEY },
      body: JSON.stringify({
        contents: [{ role: 'user', parts }],
        generationConfig: { temperature, responseMimeType: 'application/json' },
      }),
    });
      // Google returns 503 "high demand" / 429 in short bursts: retry once
      if (![429, 503].includes(res.status) || attempt === 1) break;
      await new Promise((r) => setTimeout(r, 2000));
    }
    if (!res.ok) throw new Error(`Gemini HTTP ${res.status}: ${(await res.text()).slice(0, 200)}`);
    const data = await res.json();
    const text = data?.candidates?.[0]?.content?.parts?.filter((p) => !p.thought).map((p) => p.text || '').join('') || '';
    return JSON.parse(text.replace(/^```(json)?|```$/g, '').trim());
  } finally {
    clearTimeout(timer);
  }
}

const REPORT_PROMPT = `You triage citizen flood reports for Islamabad's municipal authority.
Reports may be English, Roman Urdu or Urdu (e.g. "pani bhar gaya hai", "gaari phans gayi hai", "road band hai").
If a photo is attached, use it to estimate water depth.
Return ONLY JSON:
{"floodEvidence": number 0-1 (how strongly this indicates real flooding now),
 "depth": "shallow (<10 cm)"|"moderate (10-30 cm)"|"deep (>30 cm)"|"unknown",
 "hazard": "people_at_risk"|"vehicle_stranded"|"road_blocked"|"nuisance_flooding"|"none",
 "peopleAtRisk": boolean,
 "mentionsUnderpass": boolean,
 "summary": "one short English sentence"}`;

async function interpretReport(text, photoDataUrl) {
  const parts = [{ text: `${REPORT_PROMPT}\n\nReport text: """${String(text).slice(0, 1000)}"""` }];
  const m = /^data:(image\/[a-z+]+);base64,(.+)$/i.exec(photoDataUrl || '');
  if (m) parts.push({ inline_data: { mime_type: m[1], data: m[2] } });
  const j = await generateJSON(parts);
  const hazards = ['people_at_risk', 'vehicle_stranded', 'road_blocked', 'nuisance_flooding', 'none'];
  return {
    floodEvidence: clamp01(Number(j.floodEvidence)),
    depth: typeof j.depth === 'string' ? j.depth : 'unknown',
    hazard: hazards.includes(j.hazard) ? j.hazard : 'none',
    peopleAtRisk: Boolean(j.peopleAtRisk),
    mentionsUnderpass: Boolean(j.mentionsUnderpass),
    tags: [],
    summary: String(j.summary || '').slice(0, 200),
    engine: model(),
  };
}

const JUDGE_PROMPT = `You are the signal-fusion analyst for "Nikaasi", Islamabad's flood response system.
You receive three independent signals for one sector: citizen reports, a weather feed and a traffic feed,
plus a deterministic rule-engine score for reference. Judge how serious flooding really is.
Do not rely on citizen reports alone; weigh corroboration across signals; escalate if people are at risk.
Severity scale: Watch (<30), Moderate (30-54), High (55-74), Critical (75-100).
Return ONLY JSON:
{"score": integer 0-100, "severity": "Watch"|"Moderate"|"High"|"Critical",
 "confidence": "Low"|"Medium"|"High",
 "explanation": "one or two plain-language sentences for a duty officer, citing the concrete numbers (e.g. 'High confidence: 4 citizen reports + active red rainfall alert + traffic spike 2.1x in G-10 within the last 20 minutes')",
 "keyRisk": "short phrase"}`;

async function judgeIncident(rules, reportsSample) {
  const payload = {
    sector: rules.sector,
    citizen: { ...rules.signals.citizen, sampleReports: reportsSample },
    weather: rules.signals.weather,
    traffic: rules.signals.traffic,
    ruleEngine: { score: rules.score, severity: rules.severity, confidence: rules.confidence },
  };
  const j = await generateJSON([{ text: `${JUDGE_PROMPT}\n\nSignals:\n${JSON.stringify(payload, null, 1)}` }]);
  const score = Number.isFinite(Number(j.score)) ? Math.round(Number(j.score)) : rules.score;
  return {
    score: Math.max(0, Math.min(100, score)),
    confidence: ['Low', 'Medium', 'High'].includes(j.confidence) ? j.confidence : rules.confidence,
    explanation: String(j.explanation || '').slice(0, 400),
    keyRisk: String(j.keyRisk || '').slice(0, 80),
    engine: model(),
  };
}

function clamp01(x) { return Number.isFinite(x) ? Math.max(0, Math.min(1, x)) : 0; }

module.exports = { enabled, model, interpretReport, judgeIncident };
