#!/usr/bin/env python3
"""
GongBot 3000 - Sales Meeting Automation
Polls HubSpot for new "New Buyer Meetings" and automates Gong posts + Notion notes.
"""

import os
import json
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

# ============================================================================
# CONFIGURATION
# ============================================================================

# Set these as environment variables (do not hardcode in production)
# Example: export HUBSPOT_KEY="your-key-here"
HUBSPOT_KEY = os.environ.get("HUBSPOT_KEY", "")
HUBSPOT_OBJECT_ID = "0-421"  # New Buyer Meetings

# Notion
NOTION_KEY = os.environ.get("NOTION_KEY", "")
NOTION_PARENT_ID = os.environ.get("NOTION_PARENT_ID", "")  # L1-Call-Notes-Repo

# Slack
SLACK_KEY = os.environ.get("SLACK_KEY", "")
SLACK_CHANNEL = "test-gong"

# Polling
POLL_INTERVAL_SECONDS = 300  # 5 minutes

# State file - use /tmp for ephemeral, or current dir for persistent
# On Render, /tmp persists between requests but not between deploys
# Use the current working directory for better persistence
STATE_FILE = os.environ.get("STATE_FILE", "gongbot_state.json")

# Meetings to skip (add meeting IDs here to prevent re-processing after deletion)
# These IDs were manually deleted from HubSpot but keep getting re-processed
SKIP_MEETING_IDS = os.environ.get("SKIP_MEETING_IDS", "").split(",") if os.environ.get("SKIP_MEETING_IDS") else []

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def load_last_check():
    """Load the last check timestamp from file."""
    if Path(STATE_FILE).exists():
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
            # Verify the file has valid data
            if data.get("last_check"):
                return data
    # Default: only look for meetings from the last 24 hours on first run
    # This avoids processing old meetings when redeploying
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    return {"last_check": yesterday, "processed_ids": []}


def save_last_check(data):
    """Save the last check timestamp and processed IDs."""
    with open(STATE_FILE, "w") as f:
        json.dump(data, f)


def get_hubspot_meetings(since=None):
    """Fetch new meetings from HubSpot."""
    headers = {
        "Authorization": f"Bearer {HUBSPOT_KEY}",
        "Content-Type": "application/json"
    }
    
    all_results = []
    
    # Fetch non-archived meetings
    url = f"https://api.hubapi.com/crm/v3/objects/{HUBSPOT_OBJECT_ID}"
    params = {
        "limit": 100,
        "properties": "booking_channel,company,contact_email,contact_title,hs_appointment_name,hs_appointment_start,hs_appointment_end,hs_createdate,hs_lastmodifieddate,hs_created_by_user_id"
    }
    
    while url:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        
        data = response.json()
        all_results.extend(data.get("results", []))
        
        # Check for next page
        paging = data.get("paging", {})
        next_page = paging.get("next", {})
        if next_page:
            url = next_page.get("link")
            params = {}
        else:
            url = None
    
    # Also fetch archived meetings (HubSpot API quirk: need separate call)
    url = f"https://api.hubapi.com/crm/v3/objects/{HUBSPOT_OBJECT_ID}"
    params = {
        "limit": 100,
        "properties": "booking_channel,company,contact_email,contact_title,hs_appointment_name,hs_appointment_start,hs_appointment_end,hs_createdate,hs_lastmodifieddate,hs_created_by_user_id",
        "archived": "true"
    }
    
    while url:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        
        data = response.json()
        # Add archived property to each result
        for result in data.get("results", []):
            result["archived"] = True
        all_results.extend(data.get("results", []))
        
        # Check for next page
        paging = data.get("paging", {})
        next_page = paging.get("next", {})
        if next_page:
            url = next_page.get("link")
            params = {}
        else:
            url = None
    
    results = all_results
    
    # Filter to meetings created since last check
    if since:
        # Normalize dates for comparison (handle Z and +00:00)
        since_normalized = since.replace('Z', '+00:00')
        results = [r for r in results if 
                   r.get("properties", {}).get("hs_createdate", "").replace('Z', '+00:00') > since_normalized]
    
    return results


def get_owner_name(owner_id):
    """Get owner name from HubSpot."""
    if not owner_id:
        return "Unknown"
    
    url = f"https://api.hubapi.com/crm/v3/owners/{owner_id}"
    headers = {"Authorization": f"Bearer {HUBSPOT_KEY}"}
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            return f"{data.get('firstName', '')} {data.get('lastName', '')}".strip()
    except:
        pass
    
    return "Unknown"


def research_company(company_name):
    """Research company using Tavily."""
    # This is a simplified version - in production you'd use the actual Tavily API
    # For now, return a placeholder that triggers OpenHands AI research
    return {
        "needs_research": True,
        "company": company_name
    }



def is_meeting_processed(meeting):
    """Check if meeting was already processed by looking for a Notion page with similar name."""
    props = meeting.get("properties", {})
    company = props.get("company", "")
    logger.info(f"Checking if processed: {company} (NOTION_KEY set: {bool(NOTION_KEY)})")
    
    if not NOTION_KEY or not NOTION_PARENT_ID:
        logger.info(f"No NOTION_KEY or NOTION_PARENT_ID, allowing processing")
        return False
    
    if not company:
        return False
    
    # Search Notion for a page with this company name
    url = "https://api.notion.com/v1/search"
    headers = {
        "Authorization": f"Bearer {NOTION_KEY}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    data = {
        "query": company,
        "filter": {"property": "object", "value": "page"},
        "page_size": 5
    }
    
    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            results = response.json().get("results", [])
            # Check if any page is in our parent database
            for page in results:
                if page.get("parent", {}).get("database_id") == NOTION_PARENT_ID:
                    logger.info(f"Found existing Notion page for {company}, skipping")
                    return True
    except Exception as e:
        logger.warning(f"Error checking Notion: {e}")
    
    return False


def create_notion_page(meeting_data):
    """Create a Notion page with meeting details."""
    props = meeting_data.get("properties", {})
    
    company = props.get("company", "Unknown")
    contact_email = props.get("contact_email", "")
    contact_title = props.get("contact_title", "")
    booking_channel = props.get("booking_channel", "Unknown")
    meeting_name = props.get("hs_appointment_name", "New Buyer Meeting")
    
    # Determine if enterprise (simplified - could check employee count)
    is_enterprise = True  # Assume enterprise unless SMB
    
    # Create page title with [DRAFT] prefix
    title = f"[DRAFT] L1 {company} <> OpenHands"
    
    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {NOTION_KEY}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    
    # Build content based on template
    children = [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": "Meeting Details"}}]}
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"Contact: {contact_email} ({contact_title})"}}]}
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"Company: {company}"}}]}
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"Source: {booking_channel}"}}]}
        },
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": "Company Research"}}]}
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": "[AI Research - Pending]"}}]}
        }
    ]
    
    if is_enterprise:
        children.extend([
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {"rich_text": [{"type": "text", "text": {"content": "Discussion Questions"}}]}
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": "How are they managing developer access today?"}}]}
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": "What identity challenges are they facing?"}}]}
            }
        ])
    
    children.extend([
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": "Meeting Notes"}}]}
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": "[TBD]"}}]}
        }
    ])
    
    data = {
        "parent": {"page_id": NOTION_PARENT_ID},
        "properties": {
            "title": {"title": [{"type": "text", "text": {"content": title}}]}
        },
        "children": children
    }
    
    response = requests.post(url, headers=headers, json=data)
    
    if response.status_code == 200:
        result = response.json()
        notion_url = result.get("url", "")
        logger.info(f"Created Notion page: {notion_url}")
        return notion_url
    else:
        logger.error(f"Failed to create Notion page: {response.text}")
        return None


def get_ae_assignment(company_name, company_hq=None):
    """Assign AE based on company HQ location.
    
    Clarke: East + Northeast + Southeast + Midwest + DC + International (Europe/Brazil)
    Cliff: West + Southwest + Plains + Mountain + Asia
    """
    # If we have HQ location, use it for routing
    if company_hq:
        hq_lower = company_hq.lower()
        
        # Special case: DC is Clarke territory (not to be confused with "dc" in other words)
        if 'washington' in hq_lower and ('dc' in hq_lower or hq_lower.endswith('dc')):
            return "clarke"
        
        # Cliff's territory (West + Southwest + Plains + Mountain + Asia)
        cliff_states = {
            'ak': 'alaska', 'al': 'alabama', 'ar': 'arkansas', 'az': 'arizona', 
            'ca': 'california', 'co': 'colorado', 'hi': 'hawaii', 'ia': 'iowa', 
            'id': 'idaho', 'ks': 'kansas', 'la': 'louisiana', 'mo': 'missouri', 
            'ms': 'mississippi', 'mt': 'montana', 'nd': 'north dakota', 'ne': 'nebraska', 
            'nm': 'new mexico', 'nv': 'nevada', 'ok': 'oklahoma', 'or': 'oregon', 
            'sd': 'south dakota', 'tx': 'texas', 'ut': 'utah', 'wa': 'washington', 
            'wy': 'wyoming'
        }
        cliff_regions = ['asia', 'japan', 'china', 'india', 'singapore', 'korea', 'australia']
        
        # Check for Cliff states - must be at word boundary
        import re
        for abbrev, full_name in cliff_states.items():
            # Match state abbrev at word boundary (not as part of another word)
            if re.search(r'(?<![a-z])' + abbrev + r'(?![a-z])', hq_lower):
                return "cliff"
            # Match full name with word boundary
            if re.search(r'\b' + full_name + r'\b', hq_lower):
                return "cliff"
        
        # Check for Cliff regions (Asia, Australia, etc.)
        for region in cliff_regions:
            if re.search(r'\b' + region + r'\b', hq_lower):
                return "cliff"
        
        # Clarke's territory (East + Northeast + Southeast + Midwest + DC + International)
        return "clarke"
    
    # Default to clarke if no HQ info
    return "clarke"


def post_to_slack(meeting_data, notion_url, owner_name):
    """Post meeting info to Slack #test-gong channel."""
    props = meeting_data.get("properties", {})
    
    company = props.get("company", "Unknown")
    contact_email = props.get("contact_email", "Unknown")
    contact_title = props.get("contact_title", "")
    booking_channel = props.get("booking_channel", "Unknown")
    meeting_name = props.get("hs_appointment_name", "New Buyer Meeting")
    
    # Get booked by (the owner who created the meeting)
    booked_by = owner_name if owner_name != "Unknown" else "BDR"
    
    # Get AE assignment based on company HQ
    # Note: HubSpot meeting data may need to include company HQ field
    # For now, defaulting to clarke until HQ data is available
    ae_assignment = get_ae_assignment(company)
    
    message = f"""NEW DISCOVERY CALL BOOKED

Contact: {contact_email.split('@')[0].title()}
Title: {contact_title}
Company: {company}
Email: {contact_email}
Booked By: {booked_by}
AE: @{ae_assignment}
Source: {booking_channel}

Meeting: {meeting_name}"""
    
    if notion_url:
        message += f"\n\nNotes: {notion_url}"
    
    url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {SLACK_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "channel": SLACK_CHANNEL,
        "text": message
    }
    
    response = requests.post(url, headers=headers, json=data)
    
    if response.status_code == 200 and response.json().get("ok"):
        logger.info("Posted to Slack #test-gong")
        return True
    else:
        logger.error(f"Failed to post to Slack: {response.text}")
        return False


# ============================================================================
# MAIN LOOP
# ============================================================================

def process_meeting(meeting):
    """Process a single meeting."""
    meeting_id = meeting.get("id")
    props = meeting.get("properties", {})
    
    company = props.get("company", "Unknown")
    contact_email = props.get("contact_email", "Unknown")
    owner_id = props.get("hs_created_by_user_id")
    
    logger.info(f"Processing meeting: {company} - {contact_email}")
    
    # Get owner name
    owner_name = get_owner_name(owner_id)
    
    # Research company (placeholder - would integrate with Tavily/OpenHands AI)
    research_company(company)
    
    # Create Notion page
    notion_url = create_notion_page(meeting)
    
    # Post to Slack
    post_to_slack(meeting, notion_url, owner_name)
    
    logger.info(f"Completed processing: {meeting_id}")
    return True


def main():
    """Main polling loop."""
    logger.info("GongBot 3000 started...")
    
    state = load_last_check()
    processed_ids = state.get("processed_ids", [])
    last_check = state.get("last_check")
    
    logger.info(f"Last check: {last_check}")
    logger.info(f"Already processed: {len(processed_ids)} meetings")
    
    while True:
        try:
            # Get new meetings
            meetings = get_hubspot_meetings(since=last_check)
            logger.info(f"Total meetings from API: {len(meetings)}")
            for m in meetings:
                props = m.get("properties", {})
                logger.info(f"  - {props.get('company', 'N/A')} ({m.get('id')})")
            
            # Filter out already processed (check both local state and Notion)
            new_meetings = []
            for m in meetings:
                meeting_id = m.get("id")
                props = m.get("properties", {})
                created = props.get("hs_createdate", "")
                
                # Skip if already in local processed_ids
                if meeting_id in processed_ids:
                    continue
                
                # Skip meetings in the manual skip list (deleted from HubSpot but still in API results)
                if meeting_id in SKIP_MEETING_IDS:
                    logger.info(f"Skipping meeting {meeting_id} (in skip list)")
                    continue
                
                # Skip meetings older than 24 hours (safety net for state resets)
                from datetime import datetime, timezone, timedelta
                try:
                    created_dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
                    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
                    if created_dt < cutoff:
                        logger.info(f"Skipping {props.get('company')} - older than 24 hours")
                        continue
                except:
                    pass
                
                # Skip if already has a Notion page
                if is_meeting_processed(m):
                    processed_ids.append(meeting_id)  # Add to local state too
                    continue
                new_meetings.append(m)
            
            if new_meetings:
                logger.info(f"Found {len(new_meetings)} new meeting(s)!")
                
                for meeting in new_meetings:
                    process_meeting(meeting)
                    processed_ids.append(meeting.get("id"))
                
                # Save state
                state = {
                    "last_check": datetime.now(timezone.utc).isoformat(),
                    "processed_ids": processed_ids[-100:]  # Keep last 100
                }
                save_last_check(state)
            else:
                logger.info("No new meetings found.")
            
        except Exception as e:
            logger.error(f"Error: {e}")
        
        # Wait before next poll
        logger.info(f"Waiting {POLL_INTERVAL_SECONDS} seconds...")
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    # Start a simple HTTP server to satisfy Render's port check
    # This allows the bot to run as a web service while doing background polling
    import threading
    from http.server import HTTPServer, BaseHTTPRequestHandler
    
    class QuietHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'GongBot is running')
        def log_message(self, format, *args):
            pass  # Suppress logging
    
    # Start HTTP server in background thread
    server = HTTPServer(('0.0.0.0', int(os.environ.get('PORT', 10000))), QuietHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    
    # Run the main bot loop
    main()
