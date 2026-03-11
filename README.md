# GongBot 3000 - Sales Meeting Automation

This repo documents the setup for automating new meeting notifications to Slack (#test-gong) and creating Notion meeting notes.

## Overview

When a BDR logs a new meeting in HubSpot ("New Buyer Meetings"), this automation will:

1. Fetch the meeting from HubSpot (polls every 5 minutes)
2. Research the prospect/company with LLM *(not yet implemented)*
3. Create a Notion meeting notes page
4. Post a formatted Gong call announcement to Slack #test-gong channel

## Current Status

| Component | Status | Notes |
|-----------|--------|-------|
| Slack Bot (GongBot 3000) | ✅ Working | Posts to #test-gong |
| Notion API | ✅ Working | Creates pages in L1-Call-Notes-Repo |
| HubSpot API | ✅ Working | Fetches from object ID 0-421 |
| Render Hosting | ✅ Running | https://gongbot-e15s.onrender.com |
| GitHub Action (Wake-up) | ✅ Running | Pings Render every 5 minutes |
| LLM Company Research | ❌ Not Implemented | Placeholder only - needs integration |

## Architecture

```
[HubSpot: New Buyer Meeting]
        |
        | (polls every 5 min)
        v
[Render: GongBot 3000] ----> [LLM: Company Research] *(not implemented)*
        |
        | (creates page)
        v
[Notion: L1-Call-Notes-Repo]
        |
        | (posts message)
        v
[Slack: #test-gong]
```

## Hosting

### Render (Primary)
- **URL**: https://gongbot-e15s.onrender.com
- **Status**: Running 24/7
- **Wake-up**: GitHub Action pings every 5 minutes to prevent sleep

### GitHub Action (Keep-Awake)
- Location: `.github/workflows/keep-awake.yml`
- Runs every 5 minutes to ping Render
- Prevents Render's free tier from putting service to sleep

## API Keys & Tokens

Set these as environment variables in Render:

| Variable | Description |
|----------|-------------|
| `HUBSPOT_KEY` | HubSpot private app access token |
| `NOTION_KEY` | Notion integration token (`ntn_...`) |
| `NOTION_PARENT_ID` | Notion page/database ID for meeting notes |
| `SLACK_KEY` | Slack bot token (`xoxb-...`) |
| `OPENAI_KEY` | OpenAI API key for company research (`sk-...`) |
| `PORT` | Render port (usually 10000) |

## To Do

### High Priority
- [ ] Integrate LLM for company/prospect research (currently stub only)

### Future Enhancements
- [ ] Add HubSpot webhook for real-time meeting detection (vs polling)
- [ ] Set up Slack Workflow or Event Subscriptions for GongBot to auto-trigger
- [ ] Add company employee count detection for SMB vs Enterprise template selection

## Gong Post Format

```
NEW DISCOVERY CALL BOOKED

Contact: [Name]
Company: [Company Name]
Title: [Title]
Email: [Email]
Booked By: [BDR Name]
AE: @[AE Name]
Source: [Booking Channel]

Meeting: [Meeting Name]

Notes: [Notion Page URL]
```

## Notion Page Format

- **Title**: `L1 (Company Name) <> OpenHands`
- **Location**: L1-Call-Notes-Repo folder
- **Template**: Draft format with meeting details

## Running the Automation

### Production (Render)
The bot runs automatically on Render. No manual setup needed.

### Local Development
```bash
pip install requests
export HUBSPOT_KEY="pat-na1-..."
export NOTION_KEY="ntn_..."
export NOTION_PARENT_ID="..."
export SLACK_KEY="xoxb-..."
python gongbot.py
```

### Docker Run
```bash
docker run -d \
  --name gongbot \
  -e HUBSPOT_KEY="pat-na1-..." \
  -e NOTION_KEY="ntn_..." \
  -e SLACK_KEY="xoxb-..." \
  python:3.11-slim \
  sh -c "pip install requests && python /gongbot.py"
```

## Notes

- The "New Buyer Meetings" custom object in HubSpot uses object ID `0-421`
- Polling interval is 5 minutes (300 seconds)
- Meetings older than 24 hours are automatically skipped
- Duplicate meetings are prevented via local state file and Notion page check
