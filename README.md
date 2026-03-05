# GongBot 3000 - Sales Meeting Automation

This repo documents the setup for automating new meeting notifications to Slack (#gong) and creating Notion meeting notes.

## Overview

When a BDR logs a new meeting in HubSpot ("New Buyer Meetings"), this automation will:

1. Fetch the meeting from HubSpot
2. Research the prospect/company
3. Create a Notion meeting notes page
4. Post a formatted Gong call announcement to Slack #gong channel

## Current Status

| Component | Status | Notes |
|-----------|--------|-------|
| Slack Bot (GongBot 3000) | ✅ Working | Posts to #gong |
| OpenHands API | ✅ Working | AI processing |
| Notion API | ✅ Working | Creates pages in L1-Call-Notes-Repo |
| HubSpot API | ✅ Working | Fetches from object ID 0-421 |
| Automation Script | ✅ Ready | Polls every 5 minutes |

## API Keys & Tokens

Set these as environment variables (do not hardcode):

## To Do

### Blocked
- [ ] Enable API access for "New Buyer Meetings" custom object in HubSpot (needs object creator)

### Future Enhancements
- [ ] Set up Slack Workflow or Event Subscriptions for GongBot to auto-trigger
- [ ] Add HubSpot webhook for real-time meeting detection

## Gong Post Format

```
NEW DISCOVERY CALL BOOKED

Contact: [Name]
[LinkedIn URL]
Company: [Company Name]
Title: [Title]
HQ Location: [Location]
Company Dev Count: [Count]
AE: @[AE Name]
Source: [Source] (@[Person]) :taco:
Channel: LinkedIn
Meeting Scheduled: [Date]
Company Summary: [Company description]
Pain / Interest: [Pain points]
Reo.Dev Activity: [Activity score and signals]
```

## Notion Page Format

- **Title**: `L1 (Company Name) <> OpenHands`
- **Location**: L1-Call-Notes-Repo folder
- **Template**: SMB/MM (Light) or Enterprise (Heavy) - selected based on company size

## Architecture

```
[HubSpot]
   |
   | (Logs new "New Buyer Meeting")
   v
[OpenHands] <-- (API Key)
   |
   | (Fetches meeting details)
   v
[Notion] <-- (API Key)
   |
   | (Creates meeting notes page)
   v
[Slack #gong] <-- (GongBot 3000 Token)
   |
   | (Posts formatted Gong call)
   v
[#gong channel)
```

## Setup Steps Completed

1. ✅ Created Slack app "GongBot 3000"
2. ✅ Added `chat:write` scope
3. ✅ Installed app to workspace
4. ✅ Added bot to #gong channel
5. ✅ Created OpenHands API key
6. ✅ Created Notion API key
7. ✅ Created HubSpot service key with scopes
8. ✅ Verified Notion access to L1-Call-Notes-Repo
9. ✅ Tested Slack posting
10. ✅ Tested OpenHands API
11. ✅ Tested Notion API

## Notes

- The "New Buyer Meetings" custom object in HubSpot uses object ID `0-421`
- Standard HubSpot Meetings object works but includes pipeline meetings (not just net new)

## Running the Automation

### Option 1: Local Run
```bash
pip install requests
python gongbot.py
```

### Option 2: Docker Run
```bash
docker run -d \
  --name gongbot \
  -e HUBSPOT_KEY="pat-na1-..." \
  -e NOTION_KEY="ntn_..." \
  -e SLACK_KEY="xoxb-..." \
  python:3.11-slim \
  sh -c "pip install requests && python /gongbot.py"
```

### Option 3: Cron Job
Add to crontab:
```bash
*/5 * * * * /usr/bin/python3 /path/to/gongbot.py >> /var/log/gongbot.log 2>&1
```

The script will:
1. Poll HubSpot every 5 minutes for new "New Buyer Meetings"
2. Create Notion page with company details
3. Post to Slack #gong channel
4. Track processed meetings to avoid duplicates
