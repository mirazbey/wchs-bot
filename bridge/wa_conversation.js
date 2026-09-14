'use strict';

const { randomUUID } = require('node:crypto');
const IGA_JID = '904441442@s.whatsapp.net';
const normalizeFlight = value => String(value || '').toUpperCase().replace(/\s/g, '').replace(/^([A-Z0-9]{2})0+(?=\d)/, '$1');
const fold = value => String(value || '').toLocaleLowerCase('tr-TR').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/ı/g, 'i');
const istDate = (date = new Date()) => new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Istanbul', year: 'numeric', month: '2-digit', day: '2-digit' }).format(date);

// Read actual message bodies and choices; quoted messages and protocol metadata
// must never be mistaken for a new gate response.
function readMessage(message) {
  let m = message || {};
  for (let i = 0; i < 5; i++) {
    const inner = m.ephemeralMessage?.message || m.viewOnceMessage?.message || m.viewOnceMessageV2?.message;
    if (!inner) break;
    m = inner;
  }
  const template = m.templateMessage?.hydratedTemplate || m.templateMessage?.hydratedFourRowTemplate;
  const texts = [m.conversation, m.extendedTextMessage?.text, m.imageMessage?.caption,
    m.videoMessage?.caption, m.listMessage?.description, m.listMessage?.title,
    m.buttonsMessage?.contentText, m.interactiveMessage?.body?.text,
    template?.hydratedContentText].filter(Boolean);
  const choices = [];
  for (const section of m.listMessage?.sections || []) {
    for (const row of section.rows || []) choices.push({ title: row.title, id: row.rowId, kind: 'list' });
  }
  for (const b of m.buttonsMessage?.buttons || []) {
    choices.push({ title: b.buttonText?.displayText, id: b.buttonId, kind: 'plain' });
  }
  for (const b of template?.hydratedButtons || []) {
    if (b.quickReplyButton) choices.push({ title: b.quickReplyButton.displayText, id: b.quickReplyButton.id, index: b.index, kind: 'template' });
    if (b.urlButton?.url) texts.push(b.urlButton.url);
  }
  for (const b of m.interactiveMessage?.nativeFlowMessage?.buttons || []) {
    try {
      const p = JSON.parse(b.buttonParamsJson);
      if (b.name === 'cta_url' && p.url) texts.push(p.url);
      if (b.name === 'quick_reply') choices.push({ title: p.display_text, id: p.id, kind: 'native', name: b.name });
      if (b.name === 'single_select') {
        for (const section of p.sections || []) {
          for (const row of section.rows || []) choices.push({ title: row.title, id: row.id, kind: 'native', name: b.name });
        }
      }
    } catch { /* Unsupported choice is not guessed. */ }
  }
  const context = Object.values(m).find(v => v && typeof v === 'object' && v.contextInfo)?.contextInfo;
  return { text: texts.join('\n'), choices: choices.filter(c => c.title && c.id), quotedId: context?.stanzaId };
}

function parseGate(text) {
  let decoded = text;
  try { decoded = decodeURIComponent(text); } catch { /* Keep non-URL text. */ }
  const normalized = fold(decoded);
  const gates = new Set();
  // Require a gate label or an explicit map destination; bag belts are not gates.
  const regex = /(?:ucus kapiniz|kapiniz|kapisi|kapi(?:\s*(?:numarasi|no))?|terminal\s*-\s*gate|(?:flight\s+)?gate(?:\s+(?:is|number))?|endstoreid\s*=)\s*[:：=\-*]*\s*([a-g]\s*\d{1,2}(?:\s*[a-z])?)(?![a-z0-9])/g;
  for (const match of normalized.matchAll(regex)) {
    const gate = match[1].replace(/\s/g, '').toUpperCase();
    // A suffix separated from the number must be a standalone letter.
    if (/^[A-G]\d{1,2}[A-Z]?$/.test(gate)) gates.add(gate);
  }
  if (gates.size === 1) return { status: 'confirmed', gate: [...gates][0] };
  if (gates.size > 1) return { status: 'ambiguous', gate: null };
  if (/not available yet|not announced|henuz (?:aciklanmadi|belirlenmedi)|belirlenmemistir/.test(normalized)) return { status: 'not_announced', gate: null };
  if (/bulunamadi|bulamadi|bulamadim|kayit bulunmamaktadir|boyle bir ucus|gecersiz|no flight found|tekrar yazarak yeniden denemek/.test(normalized)) return { status: 'not_found', gate: null };
  const pier = normalized.match(/(?:ucus kapiniz|kapiniz|kapi|gate(?: is)?)\s*[:：=-]*\s*([a-g])(?![a-z0-9])/);
  return pier ? { status: 'partial', gate: null, pier: pier[1].toUpperCase() } : null;
}

class GateConversation {
  constructor({ send, connected = () => true, now = Date.now, setTimer = setTimeout,
    clearTimer = clearTimeout, stepMs = 30000, totalMs = 120000, quietMs = 4000,
    cacheMs = 120000, maxJobs = 8, trustedJids = [], autoConsent = false, logger = null } = {}) {
    Object.assign(this, { send, connected, now, setTimer, clearTimer, stepMs, totalMs, quietMs, cacheMs, maxJobs, autoConsent, logger });
    this.trusted = new Set([IGA_JID, '13817027252425@lid', ...trustedJids]);
    this.jobs = new Map(); this.cache = new Map(); this.queue = []; this.active = null;
    this.seen = new Set(); this.quietUntil = 0; this.pumpTimer = null;
    this.liveAgentUntil = 0;
    this.needsCorrelation = false;
    this.events = Promise.resolve();
  }

  start(flight, date = istDate(), direction = 'arrival') {
    flight = normalizeFlight(flight);
    if (!/^[A-Z0-9]{2}\d{1,4}$/.test(flight) || !/^\d{4}-\d{2}-\d{2}$/.test(date) ||
      !['arrival', 'departure'].includes(direction) || Number.isNaN(Date.parse(date + 'T12:00:00Z')) ||
      new Date(date + 'T12:00:00Z').toISOString().slice(0, 10) !== date) {
      return { success: false, status: 'invalid_request', error: 'Geçersiz uçuş, tarih veya yön.' };
    }
    if (this.liveAgentUntil && this.now() < this.liveAgentUntil) {
      const remainingMin = Math.ceil((this.liveAgentUntil - this.now()) / 60000);
      return { success: false, status: 'live_agent_active', error: `iGA canlı destek devrede. ${remainingMin} dk boyunca mesaj gönderimi durduruldu.` };
    }
    // Purge finished jobs, retaining enough time for polling clients.
    for (const [id, j] of this.jobs) if (j.finishedAt && this.now() - j.finishedAt > 600000) this.jobs.delete(id);
    for (const [key, c] of this.cache) if (this.now() - c.time >= this.cacheMs) this.cache.delete(key);
    const key = `${flight}|${date}|${direction}`;
    const cached = this.cache.get(key);
    if (cached) return { ...cached.result, fromCache: true };
    for (const j of this.jobs.values()) if (j.key === key && !j.result) return this.view(j.id);
    if (!this.connected()) return { success: false, status: 'disconnected', error: 'WhatsApp köprüsü bağlı değil.' };
    if (this.queue.length + (this.active ? 1 : 0) >= this.maxJobs) return { success: false, status: 'busy', error: 'WhatsApp sorgu sırası dolu.' };
    const job = { id: randomUUID(), key, flight, date, direction, state: 'queued', createdAt: this.now(),
      replied: new Set(), outbound: new Set(), correlated: false };
    this.jobs.set(job.id, job); this.queue.push(job);
    this.pump();
    return this.view(job.id);
  }

  view(id) {
    const j = this.jobs.get(id);
    if (!j) return null;
    return j.result || { success: false, pending: true, jobId: j.id, flight: j.flight,
      date: j.date, direction: j.direction, status: j.state, position: this.queue.indexOf(j) + 1 };
  }

  pump() {
    if (this.active || !this.queue.length) return;
    if (this.now() < this.quietUntil) {
      this.clearTimer(this.pumpTimer);
      this.pumpTimer = this.setTimer(() => this.pump(), this.quietUntil - this.now());
      return;
    }
    const j = this.queue.shift(); this.active = j; j.startedAt = this.now();
    j.deadline = this.now() + this.totalMs;
    this.sendStep(j, IGA_JID, { text: `${j.flight} uçuşunu takip etmek istiyorum` }, 'waiting_reply');
  }

  arm(j) {
    this.clearTimer(j.timer);
    j.timer = this.setTimer(() => {
      if (this.active !== j) return;
      this.needsCorrelation = true;
      this.finish(j, { success: false, status: j.partial ? 'partial' : 'timeout', gate: null,
        error: j.partial ? 'Tam kapı numarası doğrulanamadı.' : 'WhatsApp yanıtı zamanında tamamlanmadı.' });
    }, Math.max(1, Math.min(this.stepMs, j.deadline - this.now())));
  }

  isTrusted(sender, alt) {
    const list = [sender, alt].filter(Boolean);
    for (const raw of list) {
      const clean = raw.replace(/:.*@/, '@');
      if (clean === IGA_JID) return true;
      if (clean.includes('13817027252425')) return true;
      if (clean.includes('904441442') || clean.includes('4441442')) return true;
      if (this.trusted.has(raw) || this.trusted.has(clean)) return true;
    }
    return false;
  }

  async sendStep(j, jid, content, state, quoted) {
    if (this.active !== j) return;
    j.state = state; this.arm(j);
    this.logger?.('send_step', { flight: j.flight, jid, state, preview: content.text || content.nativeReply?.title || content.buttonReply?.displayText || Object.keys(content) });
    try {
      const sent = await this.send(jid, content, quoted);
      if (sent?.key?.id) j.outbound.add(sent.key.id);
    } catch (err) {
      this.logger?.('send_step_error', { flight: j.flight, error: err.message });
      if (this.active === j) {
        this.needsCorrelation = true;
        this.finish(j, { success: false, status: 'send_failed', error: 'WhatsApp mesajı gönderilemedi.' });
      }
    }
  }

  finish(j, result) {
    if (this.active !== j) return;
    this.clearTimer(j.timer);
    this.logger?.('finish_job', { flight: j.flight, status: result.status, gate: result.gate, error: result.error });
    j.result = { ...result, pending: false, jobId: j.id, flight: j.flight, date: j.date,
      direction: j.direction, checkedAt: new Date(this.now()).toISOString() };
    j.finishedAt = this.now(); j.state = result.status;
    if (result.status === 'confirmed') this.cache.set(j.key, { time: this.now(), result: j.result });
    this.active = null;
    // Let delayed extra messages drain before starting the next conversation.
    this.quietUntil = this.now() + this.quietMs;
    this.pump();
  }

  disconnect() {
    this.needsCorrelation = true;
    const active = this.active;
    // Empty the queue before finish() can start another request.
    const waiting = this.queue.splice(0);
    for (const j of waiting) {
      j.finishedAt = this.now();
      j.result = { success: false, pending: false, jobId: j.id, flight: j.flight, status: 'disconnected', error: 'WhatsApp bağlantısı kesildi.' };
    }
    if (active) this.finish(active, { success: false, status: 'disconnected', error: 'WhatsApp bağlantısı kesildi.' });
  }

  receive(msg, type = 'notify') {
    this.events = this.events.then(() => this.handle(msg, type)).catch(err => {
      this.logger?.('receive_error', { error: err.message });
      if (this.active) {
        this.needsCorrelation = true;
        this.finish(this.active, { success: false, status: 'processing_error', error: 'WhatsApp yanıtı işlenemedi.' });
      }
    });
    return this.events;
  }

  async handle(msg, type) {
    if (type !== 'notify' || msg.key?.fromMe || !msg.message) return;
    const sender = msg.key?.remoteJid || '';
    const alt = msg.key?.remoteJidAlt || '';
    if (!this.isTrusted(sender, alt)) {
      this.logger?.('ignored_untrusted', { sender, alt });
      return;
    }
    const cleanSender = sender.replace(/:.*@/, '@');
    if (cleanSender.endsWith('@lid')) this.trusted.add(cleanSender);
    const id = msg.key?.id;
    if (!id || this.seen.has(id)) return;
    this.seen.add(id);
    if (this.seen.size > 1000) this.seen.delete(this.seen.values().next().value);
    const j = this.active;
    const { text, choices, quotedId } = readMessage(msg.message);
    const normalized = fold(text);
    if (!j) {
      if (/musteri temsilci|canli destek|destek ekibi|temsilcimiz|ibrahim bey|ulasıyor|ulasiyor/i.test(normalized)) {
        this.liveAgentUntil = this.now() + 15 * 60 * 1000;
        this.queue = [];
        this.logger?.('detected_live_agent_while_idle', { text: (text || '').slice(0, 80) });
      }
      this.logger?.('ignored_no_active_job', { sender, text: (text || '').slice(0, 80) });
      this.pump(); return;
    }
    const timestamp = Number(msg.messageTimestamp) * 1000;
    if (Number.isFinite(timestamp) && timestamp < (j.startedAt - 60000)) {
      this.logger?.('ignored_clock_skew', { timestamp, startedAt: j.startedAt });
      return;
    }
    if (/musteri temsilci|canli destek|destek ekibi|temsilcimiz/i.test(normalized)) {
      this.logger?.('detected_live_agent_redirect', { flight: j?.flight });
      this.liveAgentUntil = this.now() + 15 * 60 * 1000;
      this.queue = [];
      this.finish(j, { success: false, status: 'live_agent_redirect', gate: null, error: 'iGA canlı desteğe yönlendirdi. Mesaj gönderimi 15 dk durduruldu.' });
      return;
    }
    const flights = [...text.toUpperCase().matchAll(/\b([A-Z]{2}\s*\d{1,4})\b/g)].map(m => normalizeFlight(m[1]));
    if (flights.length && !flights.includes(j.flight)) {
      this.logger?.('ignored_other_flight', { flights, expected: j.flight });
      return;
    }
    if (flights.includes(j.flight) || (quotedId && j.outbound.has(quotedId))) j.correlated = true;
    this.logger?.('msg_matched', { flight: j.flight, textPreview: (text || '').slice(0, 100), choices: choices.map(c => c.title) });

    const result = parseGate(text);
    if (result && (!this.needsCorrelation || j.correlated)) {
      if (result.status === 'confirmed') {
        this.needsCorrelation = false;
        this.finish(j, { success: true, ...result }); return;
      }
      if (['not_announced', 'not_found', 'ambiguous'].includes(result.status)) {
        this.finish(j, { success: false, ...result, error: 'Tam kapı bilgisi alınamadı.' }); return;
      }
      j.partial = true;
      if (!j.replied.has('partial')) {
        j.replied.add('partial'); j.state = 'waiting_exact_gate'; this.arm(j);
      }
      return;
    }
    if (/kvkk/.test(normalized) && /onay|kabul/.test(normalized)) {
      if (!this.autoConsent) {
        this.finish(j, { success: false, status: 'consent_required', error: 'iGA WhatsApp hesabında KVKK onayı gerekiyor.' }); return;
      }
      return this.reply(j, sender, msg, 'consent', choices.find(c => /onayliyorum|kabul ediyorum/.test(fold(c.title))), 'Onaylıyorum');
    }
    const today = istDate(new Date(this.now()));
    const offset = Math.round((Date.parse(j.date) - Date.parse(today)) / 86400000);
    const dayPattern = offset === 0 ? /bugun|today/ : offset === -1 ? /dun|yesterday/ : offset === 1 ? /yarin|tomorrow/ : null;
    const dateFallback = offset === 0 ? 'Bugün' : offset === -1 ? 'Dün' : offset === 1 ? 'Yarın' : 'Bugün';
    const dateChoice = dayPattern && choices.find(c => dayPattern.test(fold(c.title)));
    if (dateChoice || /ucus tarihinizi sec|when is your flight|ucusunuz ne zaman|hangi tarih/.test(normalized)) {
      return this.reply(j, sender, msg, 'date', dateChoice, dateFallback);
    }
    const directionChoice = choices.find(c => (j.direction === 'arrival' ? /gelis|gelen|arrival/ : /gidis|giden|departure/).test(fold(c.title)));
    if (directionChoice) return this.reply(j, sender, msg, 'direction', directionChoice, j.direction === 'arrival' ? 'Gelen' : 'Giden');
    if (/ucus (?:numara|kod).*(?:gir|yaz|paylas)|enter.*flight.*number/.test(normalized)) {
      if (/yeniden denemek|tekrar/.test(normalized)) {
        this.finish(j, { success: false, status: 'not_found', gate: null, error: 'Uçuş iGA sisteminde bulunamadı.' });
        return;
      }
      return this.reply(j, sender, msg, 'flight', null, j.flight);
    }
    if (/arastiriyorum|bekleyin|searching|kontrol ediyorum/.test(normalized) && !j.replied.has('progress')) {
      j.replied.add('progress'); j.state = 'searching'; this.arm(j);
    }
  }

  async reply(j, sender, msg, step, choice, fallback) {
    if (j.replied.has(step)) return;
    j.replied.add(step);
    const targetJid = IGA_JID;
    const textToSend = choice?.title || fallback || '';
    const content = { text: textToSend };
    this.logger?.('replying_step', { flight: j.flight, step, targetJid, textToSend });
    await this.sendStep(j, targetJid, content, `waiting_after_${step}`, msg);
  }
}

module.exports = { GateConversation, readMessage, parseGate, normalizeFlight, istDate, IGA_JID };
