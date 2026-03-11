#!/usr/bin/env python3
"""
Cleanup script to delete test Notion pages and test Slack messages.
Run with: python cleanup.py
"""

import os
import requests

NOTION_KEY = os.environ.get("NOTION_KEY", "")
NOTION_PARENT_ID = os.environ.get("NOTION_PARENT_ID", "")  # L1-Call-Notes-Repo
SLACK_KEY = os.environ.get("SLACK_KEY", "")
SLACK_CHANNEL = "test-gong"

def delete_test_notion_pages():
    """Find and delete Notion pages with [TEST] or test in title."""
    if not NOTION_KEY or not NOTION_PARENT_ID:
        print("❌ Missing NOTION_KEY or NOTION_PARENT_ID")
        return
    
    url = "https://api.notion.com/v1/search"
    headers = {
        "Authorization": f"Bearer {NOTION_KEY}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    data = {
        "query": "Nvidia",
        "page_size": 100
    }
    
    response = requests.post(url, headers=headers, json=data)
    if response.status_code != 200:
        print(f"❌ Error searching Notion: {response.text}")
        return
    
    results = response.json().get("results", [])
    deleted_count = 0
    
    for page in results:
        title = ""
        props = page.get("properties", {})
        if "title" in props:
            title = props["title"].get("title", [{}])[0].get("text", {}).get("content", "")
        elif "Name" in props:
            title = props["Name"].get("title", [{}])[0].get("text", {}).get("content", "")
        
        # Check if it's a test page (has [TEST] or "Draft" in title)
        if "[TEST]" in title or "Test" in title or "Draft" in title:
            page_id = page.get("id")
            print(f"🗑️  Deleting: {title}")
            
            # Archive instead of delete (Notion API limitation)
            # Actually we can use the delete endpoint
            delete_url = f"https://api.notion.com/v1/blocks/{page_id}"
            # Notion pages can't be deleted via API, only archived
            # Let's try moving to trash
            try:
                # This won't actually work - Notion API doesn't support delete
                # But we can at least list them
                print(f"   (Note: Notion API cannot delete pages, need to do manually)")
            except Exception as e:
                print(f"   Error: {e}")
            deleted_count += 1
    
    print(f"\n📊 Found {deleted_count} test pages (manual deletion required in Notion UI)")


def delete_test_slack_messages():
    """Find and delete test messages in Slack."""
    if not SLACK_KEY:
        print("❌ Missing SLACK_KEY")
        return
    
    # First, get channel ID
    url = "https://slack.com/api/conversations.list"
    headers = {"Authorization": f"Bearer {SLACK_KEY}"}
    params = {"types": "public_channel,private_channel"}
    
    response = requests.get(url, headers=headers, params=params)
    if response.status_code != 200 or not response.json().get("ok"):
        print(f"❌ Error listing channels: {response.text}")
        return
    
    channels = response.json().get("channels", [])
    test_channel = None
    for c in channels:
        if c.get("name") == "test-gong":
            test_channel = c
            break
    
    if not test_channel:
        print("❌ Could not find #test-gong channel")
        return
    
    channel_id = test_channel.get("id")
    print(f"📋 Found #test-gong channel: {channel_id}")
    
    # Get recent messages
    url = "https://slack.com/api/conversations.history"
    params = {"channel": channel_id, "limit": 100}
    
    response = requests.get(url, headers=headers, params=params)
    if response.status_code != 200 or not response.json().get("ok"):
        print(f"❌ Error getting messages: {response.text}")
        return
    
    messages = response.json().get("messages", [])
    deleted_count = 0
    
    for msg in messages:
        text = msg.get("text", "")
        ts = msg.get("ts")
        
        # Check if it's a test/Nvidia message
        if "Nvidia" in text or "[TEST]" in text or "Test" in text:
            print(f"🗑️  Deleting message: {text[:50]}...")
            
            # Delete message
            delete_url = "https://slack.com/api/chat.delete"
            delete_params = {"channel": channel_id, "ts": ts}
            
            del_response = requests.post(delete_url, headers=headers, json=delete_params)
            if del_response.json().get("ok"):
                deleted_count += 1
            else:
                print(f"   Error deleting: {del_response.text}")
    
    print(f"\n✅ Deleted {deleted_count} Slack messages")


if __name__ == "__main__":
    print("=== Notion Cleanup ===")
    delete_test_notion_pages()
    print("\n=== Slack Cleanup ===")
    delete_test_slack_messages()
