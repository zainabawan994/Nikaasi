'use strict';
/**
 * The "monsoon evening in G-10" demo script. Offsets are simulated minutes
 * after Play. Bots post the kind of messages residents actually write.
 */
const G10_MONSOON = [
  { at: 0, storm: { center: 'G-10', peak: 6, alerts: {} }, log: 'Evening rush hour. Light drizzle over western sectors.' },
  { at: 2, storm: { center: 'G-10', peak: 34, alerts: { orange: ['G-10', 'G-11', 'F-10', 'H-10', 'F-11', 'H-11'] } }, log: 'PMD (mock) issues ORANGE heavy-rain alert for western sectors.' },
  { at: 5, storm: { center: 'G-10', peak: 62, alerts: { red: ['G-10', 'G-11'], orange: ['F-10', 'H-10', 'G-9', 'F-11', 'H-11'] } }, log: 'PMD (mock) upgrades to RED: cloudburst-intensity rain over G-10 / G-11.' },
  { at: 4, report: { sector: 'F-8', reporterId: 'bot-sana', name: 'Sana', text: 'Barish bohat tez hai, gali mein thora sa pani hai.' } },
  { at: 5, report: { sector: 'G-10', reporterId: 'bot-imran', name: 'Imran', text: 'Pani bhar gaya hai Street 41 G-10/4 mein, ghutno tak aa gaya hai. Gaari phans gayi hai ghar ke bahar.' } },
  { at: 7, report: { sector: 'G-10', reporterId: 'bot-ayesha', name: 'Ayesha', text: 'Gaari phans gayi hai underpass ke paas, nikal nahi rahi.' } },
  { at: 8, report: { sector: 'G-10', reporterId: 'bot-bilal', name: 'Bilal', text: 'Road band hai — Srinagar highway G-10 underpass pe pani hi pani.' } },
  { at: 10, report: { sector: 'G-10', reporterId: 'bot-hamza', name: 'Hamza', text: 'Motorcyclist gir gaya underpass mein, koi madad karo! Pani bohat hai.' } },
  { at: 11, report: { sector: 'G-10', reporterId: 'bot-farah', name: 'Farah', text: 'Nala overflow ho raha hai G-10/2 markaz ke peeche, drain block hai.' } },
  { at: 32, storm: { center: 'G-10', peak: 24, alerts: { orange: ['G-10', 'G-11'], yellow: ['F-10', 'H-10', 'G-9'] } }, log: 'Rain easing. PMD (mock) downgrades to ORANGE.' },
  { at: 48, storm: { center: 'G-10', peak: 4, alerts: { yellow: ['G-10'] } }, log: 'Rain tapering off. Yellow advisory remains for G-10.' },
  { at: 60, storm: { center: 'G-10', peak: 0, alerts: {} }, log: 'Rain stopped. All weather alerts withdrawn.' },
];

module.exports = { G10_MONSOON };
