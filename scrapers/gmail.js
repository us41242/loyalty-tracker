// Gmail 2FA code fetcher via Google API
// For GitHub Actions, we use a Google service account or OAuth refresh token.
// For local runs, falls back to manual input.

const https = require('https');

async function fetch2FACode(accessToken) {
  if (!accessToken) {
    return manualCodeEntry();
  }

  console.log('  ⏳ Polling Gmail for Caesars MFA code...');

  for (let attempt = 0; attempt < 12; attempt++) {
    await sleep(5000);

    try {
      const messages = await gmailSearch(accessToken,
        'from:email@email.caesars-marketing.com subject:"MFA Code" newer_than:5m');

      if (messages.length > 0) {
        const msg = await gmailGetMessage(accessToken, messages[0].id);
        const code = extractCode(msg);
        if (code) {
          console.log(`  📧 Got 2FA code: ${code}`);
          return code;
        }
      }
    } catch (err) {
      console.log(`  Attempt ${attempt + 1}/12 - waiting for email...`);
    }
  }

  console.log('  ⚠️ Timed out waiting for email, falling back to manual entry');
  return manualCodeEntry();
}

function gmailSearch(accessToken, query) {
  return new Promise((resolve, reject) => {
    const url = `https://gmail.googleapis.com/gmail/v1/users/me/messages?q=${encodeURIComponent(query)}&maxResults=1`;
    const req = https.get(url, {
      headers: { Authorization: `Bearer ${accessToken}` }
    }, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        const json = JSON.parse(data);
        resolve(json.messages || []);
      });
    });
    req.on('error', reject);
  });
}

function gmailGetMessage(accessToken, messageId) {
  return new Promise((resolve, reject) => {
    const url = `https://gmail.googleapis.com/gmail/v1/users/me/messages/${messageId}?format=full`;
    const req = https.get(url, {
      headers: { Authorization: `Bearer ${accessToken}` }
    }, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => resolve(JSON.parse(data)));
    });
    req.on('error', reject);
  });
}

function extractCode(message) {
  // Check snippet first
  if (message.snippet) {
    const match = message.snippet.match(/\b(\d{6})\b/);
    if (match) return match[1];
  }
  // Check body
  const payload = message.payload;
  if (payload && payload.body && payload.body.data) {
    const body = Buffer.from(payload.body.data, 'base64').toString('utf8');
    const match = body.match(/\b(\d{6})\b/);
    if (match) return match[1];
  }
  // Check parts
  if (payload && payload.parts) {
    for (const part of payload.parts) {
      if (part.body && part.body.data) {
        const body = Buffer.from(part.body.data, 'base64').toString('utf8');
        const match = body.match(/\b(\d{6})\b/);
        if (match) return match[1];
      }
    }
  }
  return null;
}

function manualCodeEntry() {
  // In GitHub Actions this won't work — the action will need the Gmail API token
  if (process.env.CI) {
    console.error('  ❌ Cannot prompt for 2FA code in CI environment');
    return null;
  }
  const readline = require('readline');
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  return new Promise((resolve) => {
    rl.question('  Enter 2FA code from email: ', (answer) => {
      rl.close();
      resolve(answer.trim() || null);
    });
  });
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

module.exports = { fetch2FACode };
