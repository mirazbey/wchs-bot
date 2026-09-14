const express = require('express');
const QRCode = require('qrcode');
const pino = require('pino');
const path = require('node:path');
const { GateConversation, readMessage, istDate, IGA_JID } = require('./wa_conversation');

const app = express();
app.use(express.json());
const PORT = process.env.PORT || 5005;

let sock = null;
let isConnected = false;
let currentQR = null;
let baileys;

const recentLogs = [];
function logBridge(tag, data) {
  const item = { time: new Date().toISOString(), tag, ...data };
  recentLogs.push(item);
  if (recentLogs.length > 60) recentLogs.shift();
  console.log(`[WA-BRIDGE] ${tag}:`, JSON.stringify(data));
}

const engine = new GateConversation({
  connected: () => isConnected,
  stepMs: Math.max(25000, Number(process.env.WA_STEP_TIMEOUT_MS) || 30000),
  totalMs: Math.max(120000, Number(process.env.WA_TOTAL_TIMEOUT_MS) || 120000),
  trustedJids: (process.env.IGA_TRUSTED_JIDS || '13817027252425@lid').split(',').map(s => s.trim()).filter(Boolean),
  autoConsent: process.env.WA_AUTO_CONSENT === 'true',
  logger: logBridge,
  send: async (jid, content, quoted) => {
    if (!isConnected || !sock) throw new Error('disconnected');
    logBridge('socket_send', {
      jid,
      type: content.nativeReply ? 'nativeReply' : content.listReply ? 'listReply' : content.buttonReply ? 'buttonReply' : 'text',
      text: content.text || content.nativeReply?.title || content.buttonReply?.displayText || content.listReply?.title || ''
    });
    if (content.nativeReply) {
      const { name, id, title } = content.nativeReply;
      try {
        const generated = baileys.generateWAMessageFromContent(jid, {
          interactiveResponseMessage: {
            body: { text: title, format: 1 },
            nativeFlowResponseMessage: { name, paramsJson: JSON.stringify({ id }), version: 3 }
          }
        }, { userJid: sock.user.id, quoted });
        await sock.relayMessage(jid, generated.message, { messageId: generated.key.id });
        return generated;
      } catch (err) {
        logBridge('native_reply_failed_falling_back_to_text', { error: err.message, title });
        return sock.sendMessage(jid, { text: title }, quoted ? { quoted } : {});
      }
    }
    return sock.sendMessage(jid, content, quoted ? { quoted } : {});
  }
});

async function connectToWhatsApp() {
  baileys = await import('@whiskeysockets/baileys');
  const { state, saveCreds } = await baileys.useMultiFileAuthState(process.env.WA_AUTH_DIR || path.join(__dirname, 'auth_info'));
  const connection = baileys.default({ auth: state, logger: pino({ level: 'silent' }), browser: ['WCHS Radar', 'Chrome', '1.0.0'] });
  sock = connection;
  connection.ev.on('creds.update', saveCreds);
  connection.ev.on('connection.update', ({ connection: status, lastDisconnect, qr }) => {
    if (sock !== connection) return;
    if (qr) { currentQR = qr; console.log('[WA] QR hazır: /qr'); }
    if (status === 'close') {
      isConnected = false;
      engine.disconnect();
      const reconnect = lastDisconnect?.error?.output?.statusCode !== baileys.DisconnectReason.loggedOut;
      console.log('[WA] disconnected; reconnect:', reconnect);
      if (reconnect) setTimeout(() => connectToWhatsApp().catch(() => console.error('[WA] reconnect_failed')), 3000);
    } else if (status === 'open') {
      currentQR = null; isConnected = true; console.log('[WA] connected');
    }
  });
  connection.ev.on('messages.upsert', ({ messages, type }) => {
    if (sock !== connection) return;
    for (const msg of messages) {
      const from = msg.key?.remoteJid;
      const alt = msg.key?.remoteJidAlt;
      const { text, choices } = readMessage(msg.message || {});
      logBridge('incoming_msg', {
        from, alt, fromMe: msg.key?.fromMe,
        textPreview: (text || '').slice(0, 100),
        choices: (choices || []).map(c => c.title)
      });
      engine.receive(msg, type);
    }
  });
}

// Visual QR Code Web Page
app.get('/qr', async (req, res) => {
  if (isConnected) {
    return res.send(`
      <!DOCTYPE html>
      <html>
      <head>
        <meta charset="utf-8">
        <title>WhatsApp Bağlandı</title>
        <style>
          body { background:#0f172a; color:#fff; font-family:-apple-system,BlinkMacSystemFont,sans-serif; text-align:center; padding-top:80px; }
          .card { background:#1e293b; display:inline-block; padding:40px; border-radius:16px; box-shadow:0 10px 25px rgba(0,0,0,0.5); }
          .badge { font-size:48px; margin-bottom:16px; }
          h2 { margin-bottom:8px; color:#10b981; }
          p { color:#94a3b8; font-size:14px; }
        </style>
      </head>
      <body>
        <div class="card">
          <div class="badge">✅</div>
          <h2>WhatsApp Bağlantısı Aktif!</h2>
          <p>Köprü çalışıyor, bu sekmeyi kapatabilirsin.</p>
        </div>
      </body>
      </html>
    `);
  }

  if (!currentQR) {
    return res.send(`
      <!DOCTYPE html>
      <html>
      <head>
        <meta charset="utf-8"><meta http-equiv="refresh" content="3">
        <title>QR Yükleniyor...</title>
        <style>body { background:#0f172a; color:#fff; font-family:sans-serif; text-align:center; padding-top:80px; }</style>
      </head>
      <body>
        <p>⏳ QR Kod oluşturuluyor, lütfen 2 saniye bekleyin...</p>
      </body>
      </html>
    `);
  }

  try {
    const qrDataUrl = await QRCode.toDataURL(currentQR, { width: 320, margin: 2 });
    res.send(`
      <!DOCTYPE html>
      <html>
      <head>
        <meta charset="utf-8">
        <meta http-equiv="refresh" content="15">
        <title>WhatsApp Giriş Yap</title>
        <style>
          body { background:#0b0f19; color:#f1f5f9; font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif; text-align:center; padding:40px 15px; }
          .box { background:#151d30; border:1px solid #222f4c; display:inline-block; padding:30px; border-radius:16px; max-width:400px; box-shadow:0 8px 30px rgba(0,0,0,0.4); }
          h2 { font-size:20px; margin-bottom:12px; }
          ol { text-align:left; font-size:13px; color:#94a3b8; line-height:1.6; margin-bottom:20px; padding-left:20px; }
          img { border-radius:12px; background:#fff; padding:10px; box-shadow:0 4px 12px rgba(0,0,0,0.3); }
          .hint { font-size:11px; color:#64748b; margin-top:16px; }
        </style>
      </head>
      <body>
        <div class="box">
          <h2>📲 WhatsApp ile Giriş Yap</h2>
          <ol>
            <li>Telefonunda <b>WhatsApp</b>'ı aç.</li>
            <li><b>Ayarlar ➔ Bağlı Cihazlar</b> bölümüne gir.</li>
            <li><b>Cihaz Bağla</b> butonuna bas ve aşağıdaki kodu okut:</li>
          </ol>
          <img src="${qrDataUrl}" alt="WhatsApp QR Code" />
          <p class="hint">Bu kod her 15 saniyede bir otomatik yenilenir.</p>
        </div>
      </body>
      </html>
    `);
  } catch (err) {
    res.status(500).send("QR Hatası: " + err.message);
  }
});

// Queries endpoint
app.post('/queries', (req, res) => {
  const result = engine.start(req.body.flight, req.body.date || istDate(), req.body.direction || 'arrival');
  res.status(result.pending ? 202 : result.success ? 200 : 400).json(result);
});

app.get('/queries/:id', (req, res) => {
  const result = engine.view(req.params.id);
  res.status(result ? 200 : 404).json(result || { success: false, status: 'not_found', error: 'Sorgu bulunamadı.' });
});

app.delete('/queries/:id', (req, res) => {
  const job = engine.jobs.get(req.params.id);
  if (!job) return res.status(404).json({ success: false });
  if (!job.result) {
    if (engine.active === job) {
      engine.needsCorrelation = true;
      engine.finish(job, { success: false, status: 'cancelled', error: 'Sorgu iptal edildi.' });
    } else {
      engine.queue = engine.queue.filter(j => j !== job);
      job.finishedAt = Date.now();
      job.result = { success: false, pending: false, jobId: job.id, status: 'cancelled' };
    }
  }
  res.json(engine.view(job.id));
});

// Legacy single-shot gate endpoint
app.get('/gate/:flight', async (req, res) => {
  let result = engine.start(req.params.flight, req.query.date || istDate(), req.query.direction || 'arrival');
  const deadline = Date.now() + 125000;
  while (result.pending && Date.now() < deadline && !res.destroyed) {
    await new Promise(resolve => setTimeout(resolve, 500));
    result = engine.view(result.jobId);
  }
  if (!res.destroyed) res.status(result.pending ? 202 : 200).json(result);
});

// Status check
app.get('/status', (req, res) => res.json({
  connected: isConnected,
  protocol: 2,
  version: '2026-09-14-gate-flow-v3',
  cacheCount: engine.cache.size,
  queued: engine.queue.length,
  liveAgentUntil: engine.liveAgentUntil,
  liveAgentActive: Boolean(engine.liveAgentUntil && Date.now() < engine.liveAgentUntil),
  active: engine.active ? {
    flight: engine.active.flight,
    state: engine.active.state,
    elapsedMs: Date.now() - engine.active.startedAt
  } : null
}));

// Live diagnostic inspection endpoint
app.get('/debug', (req, res) => {
  res.json({
    connected: isConnected,
    version: '2026-09-14-gate-flow-v3',
    liveAgentUntil: engine.liveAgentUntil,
    liveAgentActive: Boolean(engine.liveAgentUntil && Date.now() < engine.liveAgentUntil),
    active: engine.active ? {
      id: engine.active.id,
      flight: engine.active.flight,
      state: engine.active.state,
      elapsedMs: Date.now() - engine.active.startedAt,
      replied: Array.from(engine.active.replied),
      partial: engine.active.partial
    } : null,
    queue: engine.queue.map(q => ({ flight: q.flight, state: q.state })),
    recentLogs
  });
});

app.post('/reset-live-agent', (req, res) => {
  engine.liveAgentUntil = 0;
  logBridge('live_agent_manually_reset', {});
  res.json({ success: true, liveAgentUntil: 0 });
});

// Interactive raw test send
app.get('/test-send/:text', async (req, res) => {
  if (!sock || !isConnected) return res.status(503).json({ error: 'not connected' });
  try {
    const sent = await sock.sendMessage(IGA_JID, { text: req.params.text });
    logBridge('manual_test_sent', { text: req.params.text, id: sent?.key?.id });
    res.json({ success: true, key: sent.key, sentText: req.params.text });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

if (require.main === module) {
  app.listen(PORT, '0.0.0.0', () => {
    console.log(`[*] WhatsApp Bridge port ${PORT}`);
    connectToWhatsApp().catch(() => console.error('[WA] startup_failed'));
  });
}

module.exports = { app, engine };
