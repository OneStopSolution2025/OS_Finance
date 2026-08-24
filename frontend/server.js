const express = require('express');
const path = require('path');
const app = express();

// Injects the backend URL at runtime from Railway env vars, so the same
// build works across staging/production without a rebuild.
app.get('/config.js', (req, res) => {
  const backendUrl = process.env.BACKEND_URL || 'http://localhost:8000';
  res.type('application/javascript').send(`window.OSF_API_BASE = ${JSON.stringify(backendUrl)};`);
});

app.use(express.static(__dirname));

// SPA fallback - all routes serve index.html
app.get('*', (req, res) => {
  res.sendFile(path.join(__dirname, 'index.html'));
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`OS Finances frontend running on port ${PORT}`));
