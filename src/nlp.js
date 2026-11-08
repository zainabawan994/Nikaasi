'use strict';
/**
 * Offline report interpreter (fallback when Gemini is not configured).
 * Understands English, Roman Urdu and a little Urdu script. Produces the same
 * shape Gemini returns so the fusion engine never cares which one ran.
 */

const CUES = [
  // [regex, floodEvidence 0..1, hazard tag]
  [/pani\s*(bhar|bhara|bhari|bhar\s*gaya|aa\s*gaya|khara|khada|jama)|پانی/i, 0.85, 'standing_water'],
  [/flood|flooded|waterlog|water\s*logg|submerged|inundat|sailab|سیلاب/i, 0.9, 'standing_water'],
  [/(knee|ghutno|ghutnay|kamar|waist)/i, 0.95, 'deep_water'],
  [/underpass|under\s*pass|pul\s*ke\s*neeche/i, 0.6, 'underpass'],
  [/(gaari|gari|gaadi|car|vehicle|bike|motor\s*cycle|rickshaw).{0,25}(phans|phas|stuck|band|bandh|stall|doob|dub)/i, 0.9, 'vehicle_stranded'],
  [/(phans|phas|stuck).{0,20}(gaari|gari|gaadi|car|vehicle)/i, 0.9, 'vehicle_stranded'],
  [/(gir\s*gaya|gir\s*gayi|fell|fallen|accident|injur|zakhmi|behosh|drown|doob\s*raha|help|bachao|madad)/i, 0.7, 'people_at_risk'],
  [/(road|sarak|sadak|rasta|street|gali).{0,15}(band|bandh|block|closed)/i, 0.65, 'road_blocked'],
  [/(nala|nullah|naala|drain|gutter|manhole).{0,25}(over|block|band|choke|ubal|bhar)/i, 0.8, 'drain_blocked'],
  [/(heavy|tez|bohat|shadeed).{0,10}(rain|barish|baarish)|cloudburst|موسلا/i, 0.35, 'heavy_rain'],
  [/(barish|baarish|rain)/i, 0.2, 'rain'],
];
const NEGATIONS = /(no\s*water|pani\s*nahi|nahi\s*hai\s*pani|clear\s*now|saaf\s*hai|theek\s*hai|resolved|chala\s*gaya)/i;
const MINOR = /(thora|thoda|little|slight|halka|kam\s*pani)/i;

function interpret(text, hasPhoto = false) {
  const t = String(text || '');
  const tags = new Set();
  let evidence = 0;
  CUES.forEach(([re, w, tag]) => {
    if (re.test(t)) { tags.add(tag); evidence = 1 - (1 - evidence) * (1 - w); }
  });
  if (MINOR.test(t)) { evidence *= 0.6; tags.add('minor'); }
  if (NEGATIONS.test(t)) { evidence *= 0.2; tags.add('negated'); }
  if (hasPhoto) evidence = Math.min(1, evidence + 0.08);

  let depth = 'unknown';
  if (tags.has('deep_water') || tags.has('vehicle_stranded')) depth = 'deep (>30 cm)';
  else if (tags.has('standing_water') && !tags.has('minor')) depth = 'moderate (10-30 cm)';
  else if (tags.has('minor')) depth = 'shallow (<10 cm)';

  const hazard = tags.has('people_at_risk') ? 'people_at_risk'
    : tags.has('vehicle_stranded') ? 'vehicle_stranded'
      : tags.has('road_blocked') || tags.has('deep_water') ? 'road_blocked'
        : evidence > 0.5 ? 'nuisance_flooding' : 'none';

  return {
    floodEvidence: +evidence.toFixed(2),
    depth,
    hazard,
    peopleAtRisk: tags.has('people_at_risk') && evidence > 0.4,
    mentionsUnderpass: tags.has('underpass'),
    tags: [...tags],
    summary: summarize(tags, depth),
    engine: 'lexicon-v1',
  };
}

function summarize(tags, depth) {
  const parts = [];
  if (tags.has('negated')) return 'Reporter indicates the water has cleared.';
  if (tags.has('standing_water') || tags.has('deep_water')) parts.push(`standing water, ${depth}`);
  if (tags.has('vehicle_stranded')) parts.push('vehicle(s) stranded');
  if (tags.has('people_at_risk')) parts.push('person possibly hurt / needs help');
  if (tags.has('road_blocked')) parts.push('road impassable');
  if (tags.has('underpass')) parts.push('at underpass');
  if (tags.has('drain_blocked')) parts.push('drain blocked/overflowing');
  if (!parts.length && (tags.has('rain') || tags.has('heavy_rain'))) parts.push('rain reported, no flooding described');
  return parts.length ? parts.join('; ') : 'No clear flood indicators in text.';
}

module.exports = { interpret };
