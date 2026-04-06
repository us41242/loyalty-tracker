#!/usr/bin/env python3
"""
Run this LOCALLY once to create a Chrome profile that has passed Imperva's challenge.
The profile is then uploaded to Supabase storage for GitHub Actions to use.
"""

import os, re, time, shutil, json, platform, subprocess, base64, io, zipfile
from dotenv import load_dotenv
load_dotenv()

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By

from supabase import create_client
supabase = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])

PROFILE_DIR = os.path.expanduser('~/Documents/loyalty-tracker/chrome-profile')

# Detect Chrome version
chrome_ver = None
try:
    if platform.system() == 'Darwin':
        result = subprocess.run(
            ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '--version'],
            capture_output=True, text=True)
    else:
        result = subprocess.run(['google-chrome', '--version'], capture_output=True, text=True)
    ver_match = re.search(r'(\d+)\.', result.stdout)
    chrome_ver = int(ver_match.group(1)) if ver_match else None
except:
    pass

print(f"Chrome version: {chrome_ver}")
print(f"Profile dir: {PROFILE_DIR}")

options = uc.ChromeOptions()
options.add_argument('--window-size=1920,1080')
options.add_argument('--lang=en-US')
options.add_argument(f'--user-data-dir={PROFILE_DIR}')

driver = uc.Chrome(options=options, use_subprocess=True,
                   version_main=chrome_ver) if chrome_ver else uc.Chrome(options=options, use_subprocess=True)

try:
    print("\n1. Visiting caesars.com to pass Imperva challenge...")
    print("   If you see the 'I am human' checkbox, CLICK IT in the browser window.")
    driver.get('https://www.caesars.com/rewards/home')
    print(f"   URL: {driver.current_url}")

    # Wait up to 60s for Imperva challenge to be solved (manually or auto)
    for i in range(30):
        time.sleep(2)
        text = driver.find_element(By.TAG_NAME, 'body').text[:300]
        if 'REWARD CREDITS' in text or 'Book a Room' in text.upper() or 'CAESARS REWARDS' in text:
            print(f"   ✅ Caesars loaded after {(i+1)*2}s")
            break
        if i % 5 == 0:
            print(f"   Waiting... ({(i+1)*2}s) - click 'I am human' if you see it")
    else:
        print(f"   ⚠️ Timed out, saving profile anyway")

    print("\n2. Visiting mgmresorts.com...")
    driver.get('https://www.mgmresorts.com/')
    time.sleep(10)
    print(f"   URL: {driver.current_url}")

    print("\n3. Saving profile...")
    driver.quit()
    time.sleep(2)

    # Zip the profile directory (only essential files)
    print("   Zipping profile...")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(PROFILE_DIR):
            # Skip large/unnecessary dirs
            dirs[:] = [d for d in dirs if d not in [
                'Cache', 'Code Cache', 'GPUCache', 'ShaderCache',
                'Service Worker', 'blob_storage', 'GrShaderCache',
                'component_crx_cache', 'CrashpadMetrics-active.pma',
                'BrowserMetrics', 'optimization_guide_model_store',
                'Safe Browsing', 'Crashpad'
            ]]
            for f in files:
                filepath = os.path.join(root, f)
                arcname = os.path.relpath(filepath, PROFILE_DIR)
                # Skip very large files
                try:
                    if os.path.getsize(filepath) > 10_000_000:  # 10MB
                        continue
                except:
                    continue
                try:
                    zf.write(filepath, arcname)
                except:
                    pass

    profile_bytes = buf.getvalue()
    profile_b64 = base64.b64encode(profile_bytes).decode('utf-8')
    print(f"   Profile size: {len(profile_bytes) / 1024 / 1024:.1f} MB")

    # Save to Supabase
    print("   Uploading to Supabase...")
    # Split into chunks if needed (Supabase has row size limits)
    CHUNK_SIZE = 5_000_000  # 5MB chunks
    chunks = [profile_b64[i:i+CHUNK_SIZE] for i in range(0, len(profile_b64), CHUNK_SIZE)]

    # Clear old chunks
    supabase.table('session_cookies').delete().eq('site', 'chrome_profile_meta').execute()
    for i in range(20):
        supabase.table('session_cookies').delete().eq('site', f'chrome_profile_chunk_{i}').execute()

    # Save metadata
    supabase.table('session_cookies').upsert({
        'site': 'chrome_profile_meta',
        'cookies': json.dumps({'chunks': len(chunks), 'size': len(profile_bytes)}),
        'updated_at': 'now()',
    }, on_conflict='site').execute()

    # Save chunks
    for i, chunk in enumerate(chunks):
        supabase.table('session_cookies').upsert({
            'site': f'chrome_profile_chunk_{i}',
            'cookies': json.dumps({'data': chunk}),
            'updated_at': 'now()',
        }, on_conflict='site').execute()
        print(f"   Uploaded chunk {i+1}/{len(chunks)}")

    print(f"\n✅ Profile saved to Supabase ({len(chunks)} chunks)")
    print("   GitHub Actions will now load this profile automatically.")

except Exception as e:
    print(f"\n💥 Error: {e}")
    import traceback
    traceback.print_exc()
    try:
        driver.quit()
    except:
        pass
