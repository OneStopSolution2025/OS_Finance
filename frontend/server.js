const express = require('express');
const path = require('path');
const app = express();

// Injects the backend URL and (optionally) a Sentry DSN at runtime from
// Railway env vars, so the same build works across staging/production
// without a rebuild, and Sentry stays fully optional — an empty string here
// means the frontend Sentry init below simply never runs.
app.get('/config.js', (req, res) => {
  const backendUrl = process.env.BACKEND_URL || 'http://localhost:8000';
  const sentryDsn = process.env.FRONTEND_SENTRY_DSN || '';
  res.type('application/javascript').send(
    `window.OSF_API_BASE = ${JSON.stringify(backendUrl)};\n` +
    `window.OSF_SENTRY_DSN = ${JSON.stringify(sentryDsn)};`
  );
});

app.use(express.static(__dirname));

// SPA fallback - all routes serve index.html
app.get('*', (req, res) => {
  res.sendFile(path.join(__dirname, 'index.html'));
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`OS Finances frontend running on port ${PORT}`));
