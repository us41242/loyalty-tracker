#!/bin/bash
# Daily casino rewards scraper — runs via launchd at 6am PT
# Logs to ~/Documents/loyalty-tracker/logs/

export PATH="/opt/homebrew/bin:$PATH"
cd /Users/joshuaedrake/Documents/loyalty-tracker

# Create log directory
mkdir -p logs

# Run with timestamp in log filename
LOG_FILE="logs/scrape-$(date +%Y-%m-%d).log"

echo "=== Scraper started at $(date) ===" >> "$LOG_FILE" 2>&1
/usr/bin/python3 scraper.py >> "$LOG_FILE" 2>&1
echo "=== Scraper finished at $(date) ===" >> "$LOG_FILE" 2>&1

# Keep only last 30 days of logs
find logs/ -name "*.log" -mtime +30 -delete 2>/dev/null
