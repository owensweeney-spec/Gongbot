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
from openai import OpenAI

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

# OpenAI for company research
# Accept both "OPENAI_KEY" and "OpenAI_Key" for flexibility
OPENAI_KEY = os.environ.get("OPENAI_KEY") or os.environ.get("OpenAI_Key", "")

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
        try:
            with open(STATE_FILE, "r") as f:
                content = f.read().strip()
                if not content:
                    # Empty file - return defaults
                    raise ValueError("Empty state file")
                data = json.loads(content)
                # Verify the file has valid data
                if data.get("last_check"):
                    return data
        except (json.JSONDecodeError, ValueError, IOError) as e:
            logger.warning(f"Error loading state file: {e}. Starting fresh.")
    
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
    
    # Deduplicate meetings by ID (regular + archived API calls can return same meeting)
    seen_ids = set()
    unique_results = []
    for r in results:
        meeting_id = r.get("id")
        if meeting_id not in seen_ids:
            seen_ids.add(meeting_id)
            unique_results.append(r)
    results = unique_results
    
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


def get_contact_by_email(email):
    """Look up contact in HubSpot by email to get full name."""
    if not email or not HUBSPOT_KEY:
        return None, None
    
    url = "https://api.hubapi.com/crm/v3/objects/contacts/search"
    headers = {
        "Authorization": f"Bearer {HUBSPOT_KEY}",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(url, headers=headers, json={
            "filterGroups": [{
                "filters": [{
                    "propertyName": "email",
                    "operator": "EQ",
                    "value": email
                }]
            }],
            "properties": ["firstname", "lastname", "jobtitle"]
        })
        
        if response.status_code == 200:
            data = response.json()
            results = data.get("results", [])
            if results:
                contact = results[0]
                props = contact.get("properties", {})
                first_name = props.get("firstname", "") or ""
                last_name = props.get("lastname", "") or ""
                full_name = f"{first_name} {last_name}".strip()
                job_title = props.get("jobtitle", "") or ""
                logger.info(f"Found contact in HubSpot: {full_name}, title: {job_title}")
                return full_name, job_title
    except Exception as e:
        logger.error(f"Error looking up contact: {e}")
    
    return None, None


def research_company(company_name, contact_name="", contact_title=""):
    """Research company and contact using OpenAI."""
    logger.info(f"Starting research for company: {company_name}, contact: {contact_name}, title: {contact_title}")
    logger.info(f"OPENAI_KEY set: {bool(OPENAI_KEY)}, starts with: {OPENAI_KEY[:10] if OPENAI_KEY else 'NOT SET'}")
    
    if not OPENAI_KEY:
        logger.info("No OPENAI_KEY set, skipping research")
        return {
            "company": company_name,
            "company_hq": "",
            "company_dev_count": "",
            "company_summary": "",
            "contact_background": "",
            "pain_interest": ""
        }
    
    try:
        client = OpenAI(api_key=OPENAI_KEY)
        
        # Build research query
        research_query = f"""Research {company_name}"""
        if contact_name:
            research_query += f" and their contact {contact_name}"
        if contact_title:
            research_query += f" who is a {contact_title}"
        
        research_query += """.

Provide a JSON response with these fields:
- hq_location: Company headquarters location (city, state/country)
- dev_count: Estimated number of software developers/engineers at the company (e.g., "50-100", "500+", "10-20")
- company_summary: 2-3 sentence summary of what the company does, their industry, and key products
- contact_background: 1-2 sentences about the contact's background based on their title and company
- pain_points: 1-2 potential pain points or interests related to developer tools, API access, or developer experience

If you cannot find information for a field, leave it blank."""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a research assistant. Provide accurate, concise information. If you cannot find information, say so honestly."},
                {"role": "user", "content": research_query}
            ],
            response_format={"type": "json_object"},
            max_tokens=500
        )
        
        result = json.loads(response.choices[0].message.content)
        
        logger.info(f"Research complete for {company_name}")
        logger.info(f"Research result: {result}")
        return {
            "company": company_name,
            "company_hq": result.get("hq_location", ""),
            "company_dev_count": result.get("dev_count", ""),
            "company_summary": result.get("company_summary", ""),
            "contact_background": result.get("contact_background", ""),
            "pain_interest": result.get("pain_points", "")
        }
        
    except Exception as e:
        logger.error(f"Error researching company: {e}")
        return {
            "company": company_name,
            "company_hq": "",
            "company_dev_count": "",
            "company_summary": "",
            "contact_background": "",
            "pain_interest": ""
        }



def is_meeting_processed(meeting):
    """Check if meeting was already processed by looking for a Notion page with matching company and date."""
    props = meeting.get("properties", {})
    company = props.get("company", "")
    meeting_start = props.get("hs_appointment_start", "")
    meeting_id = meeting.get("id", "")
    
    logger.info(f"Checking if processed: {company} (meeting ID: {meeting_id}) (NOTION_KEY set: {bool(NOTION_KEY)})")
    
    if not NOTION_KEY or not NOTION_PARENT_ID:
        logger.info(f"No NOTION_KEY or NOTION_PARENT_ID, allowing processing")
        return False
    
    if not company:
        return False
    
    # Extract meeting date for comparison (if available)
    meeting_date = ""
    if meeting_start:
        try:
            dt = datetime.fromisoformat(meeting_start.replace('Z', '+00:00'))
            meeting_date = dt.strftime("%Y-%m-%d")
        except:
            pass
    
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
        "page_size": 10
    }
    
    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            results = response.json().get("results", [])
            # Check if any page is in our parent database AND matches the meeting date
            for page in results:
                if page.get("parent", {}).get("database_id") == NOTION_PARENT_ID:
                    # Get page title to check date
                    page_title = ""
                    properties = page.get("properties", {})
                    if "title" in properties:
                        page_title = properties["title"].get("title", [{}])[0].get("text", {}).get("content", "")
                    
                    # If we have a meeting date, check if page was created today
                    # Page titles are like "[DRAFT] L1 Cisco <> OpenHands" - no date info
                    # So we need a different approach: check if page was created recently
                    # Get page creation time
                    created_time = page.get("created_time", "")
                    page_date = ""
                    if created_time:
                        try:
                            dt = datetime.fromisoformat(created_time.replace('Z', '+00:00'))
                            page_date = dt.strftime("%Y-%m-%d")
                        except:
                            pass
                    
                    # If page was created TODAY (or we don't know date), check if it's a duplicate
                    # We'll allow pages from today to be processed as potential duplicates
                    # but pages from other days are likely different meetings
                    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    
                    if page_date == today or not page_date:
                        # Same day - this is likely a duplicate, skip
                        logger.info(f"Found existing Notion page for {company} created today, skipping")
                        return True
                    else:
                        # Different day - this is a different meeting, allow processing
                        logger.info(f"Found existing Notion page for {company} from {page_date}, allowing (different meeting)")
    except Exception as e:
        logger.warning(f"Error checking Notion: {e}")
    
    return False


def create_notion_page(meeting_data, research=None, contact_name_override=None):
    """Create a Notion page with meeting details."""
    props = meeting_data.get("properties", {})
    
    company = props.get("company", "Unknown")
    contact_email = props.get("contact_email", "")
    contact_title = props.get("contact_title", "")
    booking_channel = props.get("booking_channel", "Unknown")
    meeting_name = props.get("hs_appointment_name", "New Buyer Meeting")
    
    # Get contact name from override or look up in HubSpot
    if contact_name_override:
        contact_name = contact_name_override
    else:
        lookup = get_contact_by_email(contact_email)
        if lookup[0]:
            contact_name = lookup[0]
        else:
            contact_name = contact_email.split('@')[0].title().replace('.', ' ')
    
    # Default research if not provided
    if research is None:
        research = {
            "company_hq": "",
            "company_dev_count": "",
            "company_summary": "",
            "contact_background": "",
            "pain_interest": ""
        }
    
    # Determine if enterprise (simplified - could check employee count)
    is_enterprise = True  # Assume enterprise unless SMB
    
    # Detect test meetings by keywords in meeting name
    test_keywords = ["test", "fake", "demo", "dummy", "sample"]
    is_test_meeting = any(keyword in meeting_name.lower() for keyword in test_keywords)
    
    # Create page title with [TEST] or [DRAFT] prefix
    if is_test_meeting:
        title = f"[TEST] L1 {company} <> OpenHands"
    else:
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
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"Contact: {contact_name} ({contact_email}) - {contact_title}"}}]}
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
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"HQ Location: {research.get('company_hq', 'Unknown')}"}}]}
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"Dev Count: {research.get('company_dev_count', 'Unknown')}"}}]}
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"Company Summary: {research.get('company_summary', 'N/A')}"}}]}
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"Contact Background: {research.get('contact_background', 'N/A')}"}}]}
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"Pain Points / Interests: {research.get('pain_interest', 'N/A')}"}}]}
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


# AE Assignment - map short names to full names
AE_NAMES = {
    "clarke": "Clarke Shipley",
    "cliff": "Cliff Yang"
}

def get_ae_assignment(company_name, company_hq=None):
    """Assign AE based on company HQ location.
    
    Clarke: East + Northeast + Southeast + Midwest + DC + International (Europe/Brazil)
    Cliff: West + Southwest + Plains + Mountain + Asia
    
    When HQ is unknown, we default to Cliff (Cliff Yang) because most tech companies
    (Cisco, Google, Apple, Meta, etc.) are West Coast. This can be overridden by
    adding company-specific overrides below.
    """
    # Known company HQ overrides - add companies here if you know their HQ
    KNOWN_COMPANY_HQ = {
        "cisco": "san jose, ca",
        "google": "mountain view, ca",
        "apple": "cupertino, ca",
        "meta": "menlo park, ca",
        "microsoft": "redmond, wa",
        "amazon": "seattle, wa",
        "salesforce": "san francisco, ca",
        "oracle": "austin, tx",
        "ibm": "armonk, ny",
        "intel": "santa clara, ca",
        "nvidia": "santa clara, ca",
        "adobe": "san jose, ca",
        "netflix": "los gatos, ca",
        "uber": "san francisco, ca",
        "airbnb": "san francisco, ca",
        "stripe": "san francisco, ca",
        "shopify": "ottawa, canada",
        "slack": "san francisco, ca",
        "zoom": "san jose, ca",
        "palo alto networks": "santa clara, ca",
        "service now": "san diego, ca",
        "workday": "pleasanton, ca",
        "vmware": "palo alto, ca",
        "salesforce": "san francisco, ca",
    }
    
    # If HQ is empty, try to find company in known list
    if not company_hq:
        company_lower = company_name.lower()
        for known_company, known_hq in KNOWN_COMPANY_HQ.items():
            if known_company in company_lower or company_lower in known_company:
                company_hq = known_hq
                logger.info(f"Found known company HQ for {company_name}: {company_hq}")
                break
    
    # If we still don't have HQ, default to Cliff (most tech companies are West Coast)
    if not company_hq:
        logger.info(f"Unknown HQ for {company_name}, defaulting to Cliff (West Coast)")
        return AE_NAMES.get("cliff", "Cliff Yang")
    
    # If we have HQ location, use it for routing
    if company_hq:
        hq_lower = company_hq.lower()
        
        # Special case: DC is Clarke territory (not to be confused with "dc" in other words)
        if 'washington' in hq_lower and ('dc' in hq_lower or hq_lower.endswith('dc')):
            ae_key = "clarke"
        else:
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
            ae_key = "clarke"  # default
            for abbrev, full_name in cliff_states.items():
                if re.search(r'(?<![a-z])' + abbrev + r'(?![a-z])', hq_lower):
                    ae_key = "cliff"
                    break
                if re.search(r'\b' + full_name + r'\b', hq_lower):
                    ae_key = "cliff"
                    break
            
            # Check for Cliff regions
            if ae_key == "clarke":
                for region in cliff_regions:
                    if re.search(r'\b' + region + r'\b', hq_lower):
                        ae_key = "cliff"
                        break
    
    return AE_NAMES.get(ae_key, "Clarke Shipley")


def post_to_slack(meeting_data, notion_url, owner_name, research=None, contact_name_override=None):
    """Post meeting info to Slack #test-gong channel."""
    props = meeting_data.get("properties", {})
    
    company = props.get("company", "Unknown")
    contact_email = props.get("contact_email", "Unknown")
    contact_title = props.get("contact_title", "")
    booking_channel = props.get("booking_channel", "Unknown")
    meeting_name = props.get("hs_appointment_name", "New Buyer Meeting")
    
    # Use contact name from override or extract from email
    if contact_name_override:
        contact_name = contact_name_override
    else:
        # Try to look up contact in HubSpot first
        lookup = get_contact_by_email(contact_email)
        if lookup[0]:
            contact_name = lookup[0]
        else:
            contact_name = contact_email.split('@')[0].title().replace('.', ' ')
    
    # Get booked by (the owner who created the meeting)
    booked_by = owner_name if owner_name != "Unknown" else "BDR"
    
    # Get research data or use defaults
    if research is None:
        research = {
            "company_hq": "",
            "company_dev_count": "",
            "company_summary": "",
            "contact_background": "",
            "pain_interest": ""
        }
    
    # Build LinkedIn URL - use people search with name + company filter
    # This is more likely to find the actual profile than just name search
    linkedin_search = f"https://www.linkedin.com/search/results/people/?keywords={contact_name.replace(' ', '%20')}&company={company.replace(' ', '%20')}"
    
    # Get AE assignment based on company HQ
    ae_assignment = get_ae_assignment(company, research.get("company_hq", ""))
    
    # Format meeting date
    meeting_start = props.get("hs_appointment_start", "")
    meeting_date = ""
    if meeting_start:
        try:
            dt = datetime.fromisoformat(meeting_start.replace('Z', '+00:00'))
            meeting_date = dt.strftime("%B %d, %Y at %I:%M %p UTC")
        except:
            meeting_date = meeting_start
    
    message = f"""NEW DISCOVERY CALL BOOKED
Contact: {contact_name} ({linkedin_search})"""
    
    # Add fields
    message += f"""

• Company: {company}
• Title: {contact_title}
• HQ Location: {research.get('company_hq', '')}
• Company Dev Count: {research.get('company_dev_count', '')}
• Source: {booking_channel}
• Meeting Scheduled: {meeting_date}
• Company Summary: {research.get('company_summary', '')}
• Contact Background: {research.get('contact_background', '')}
• Pain / Interest: {research.get('pain_interest', '')}"""
    
    if notion_url:
        message += f"\n\nNotes: {notion_url}"
    
    # Add AE assignment at the end
    message += f"\n\nAE: @{ae_assignment}"
    
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
    contact_title = props.get("contact_title", "")
    owner_id = props.get("hs_created_by_user_id")
    
    logger.info(f"Processing meeting: {company} - {contact_email}")
    
    # Get owner name
    owner_name = get_owner_name(owner_id)
    
    # Look up contact in HubSpot to get full name and accurate title
    contact_lookup = get_contact_by_email(contact_email)
    if contact_lookup[0]:  # full_name found
        contact_name = contact_lookup[0]
        # Use HubSpot title if meeting title is empty
        if not contact_title and contact_lookup[1]:
            contact_title = contact_lookup[1]
    else:
        # Fallback: extract from email
        contact_name = contact_email.split('@')[0].title().replace('.', ' ')
    
    # Research company using OpenAI
    research = research_company(company, contact_name, contact_title)
    
    # Create Notion page with research data
    notion_url = create_notion_page(meeting, research, contact_name_override=contact_name)
    
    # Post to Slack with research data
    post_to_slack(meeting, notion_url, owner_name, research, contact_name_override=contact_name)
    
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
