// One-time script to get a Gmail OAuth2 refresh token
// Run this locally, authenticate in the browser, then save the refresh token

const http = require('http');
const https = require('https');
const { URL } = require('url');

const CLIENT_ID = '480283047835-mg8onnfuce1l9qep67tlc1l4on6ihuel.apps.googleusercontent.com';
const CLIENT_SECRET = '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';
const REDIRECT_URI = 'http://localhost:3456/callback';
const SCOPES = 'https://www.googleapis.com/auth/gmail.readonly';

// Step 1: Open browser for authorization
const authUrl = `https://accounts.google.com/o/oauth2/v2/auth?` +
  `client_id=${CLIENT_ID}` +
  `&redirect_uri=${encodeURIComponent(REDIRECT_URI)}` +
  `&response_type=code` +
  `&scope=${encodeURIComponent(SCOPES)}` +
  `&access_type=offline` +
  `&prompt=consent`;

console.log('\n🔐 Gmail OAuth2 Setup\n');
console.log('Opening browser for authentication...');
console.log('If it doesn\'t open, go to:\n');
console.log(authUrl);
console.log('\n⚠️  IMPORTANT: Sign in with joshuaedrake@gmail.com\n');

// Open browser
require('child_process').exec(`open "${authUrl}"`);

// Step 2: Listen for the callback
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:3456`);

  if (url.pathname === '/callback') {
    const code = url.searchParams.get('code');

    if (!code) {
      res.writeHead(400);
      res.end('No authorization code received');
      return;
    }

    // Step 3: Exchange code for tokens
    try {
      const tokens = await exchangeCode(code);

      res.writeHead(200, { 'Content-Type': 'text/html' });
      res.end(`
        <h1>✅ Success!</h1>
        <p>You can close this window.</p>
        <p>Check your terminal for the tokens.</p>
      `);

      console.log('\n═══════════════════════════════════════');
      console.log('  ✅ TOKENS RECEIVED');
      console.log('═══════════════════════════════════════\n');
      console.log('Refresh Token:', tokens.refresh_token);
      console.log('\nAccess Token:', tokens.access_token);
      console.log('\n───────────────────────────────────────');
      console.log('\nAdd these as GitHub secrets:');
      console.log(`  GMAIL_REFRESH_TOKEN = ${tokens.refresh_token}`);
      console.log(`  GMAIL_CLIENT_ID = ${CLIENT_ID}`);
      console.log(`  GMAIL_CLIENT_SECRET = ${CLIENT_SECRET}`);
      console.log('\n───────────────────────────────────────\n');

      server.close();
      process.exit(0);
    } catch (err) {
      res.writeHead(500);
      res.end('Token exchange failed: ' + err.message);
      console.error('Error:', err.message);
    }
  }
});

server.listen(3456, () => {
  console.log('Waiting for OAuth callback on port 3456...\n');
});

function exchangeCode(code) {
  return new Promise((resolve, reject) => {
    const postData = new URLSearchParams({
      code,
      client_id: CLIENT_ID,
      client_secret: CLIENT_SECRET,
      redirect_uri: REDIRECT_URI,
      grant_type: 'authorization_code',
    }).toString();

    const req = https.request('https://oauth2.googleapis.com/token', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Content-Length': postData.length,
      },
    }, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        const json = JSON.parse(data);
        if (json.error) reject(new Error(json.error_description || json.error));
        else resolve(json);
      });
    });
    req.on('error', reject);
    req.write(postData);
    req.end();
  });
}
