"""
Microsoft Graph API Mailbox Poller for Inbound Clinical Faxes
Polls configured staff mailboxes (e.g. staff1@example.com, staff2@example.com)
and downloads incoming e-fax attachments into the PaperEpley pipeline.
"""

import base64
from pathlib import Path
from typing import List, Dict
import httpx
from config.settings import settings


class GraphClient:
    def __init__(self):
        self.tenant_id = settings.GRAPH_TENANT_ID
        self.client_id = settings.GRAPH_CLIENT_ID
        self.client_secret = settings.GRAPH_CLIENT_SECRET
        self._token = None

    async def get_access_token(self) -> str:
        """Acquires OAuth2 client credentials token from Microsoft Identity platform."""
        if not (self.tenant_id and self.client_id and self.client_secret):
            return ""
            
        url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials",
            "scope": "https://graph.microsoft.com/.default"
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, data=data)
            if resp.status_code == 200:
                self._token = resp.json().get("access_token")
                return self._token
            else:
                print(f"Graph Auth failed: {resp.status_code} {resp.text}")
                return ""

    async def fetch_inbound_faxes(self, target_dir: str) -> int:
        """
        Polls configured mailboxes for unread messages with PDF attachments.
        Downloads attachments into target_dir and marks messages as processed.
        """
        token = await self.get_access_token()
        if not token:
            return 0

        target_path = Path(target_dir)
        target_path.mkdir(parents=True, exist_ok=True)
        
        headers = {"Authorization": f"Bearer {token}"}
        downloaded = 0

        async with httpx.AsyncClient(timeout=30) as client:
            for mailbox in settings.mailbox_list:
                # Query unread messages with attachments
                url = (
                    f"https://graph.microsoft.com/v1.0/users/{mailbox}/messages"
                    f"?$filter=hasAttachments eq true and isRead eq false&$top=25"
                )
                resp = await client.get(url, headers=headers)
                if resp.status_code != 200:
                    continue
                
                messages = resp.json().get("value", [])
                for msg in messages:
                    msg_id = msg.get("id")
                    att_url = f"https://graph.microsoft.com/v1.0/users/{mailbox}/messages/{msg_id}/attachments"
                    att_resp = await client.get(att_url, headers=headers)
                    if att_resp.status_code != 200:
                        continue
                    
                    attachments = att_resp.json().get("value", [])
                    has_pdf = False
                    for att in attachments:
                        name = att.get("name", "")
                        if name.lower().endswith((".pdf", ".tif", ".tiff")):
                            content_bytes = base64.b64decode(att.get("contentBytes", ""))
                            save_file = target_path / f"fax_{msg_id[:8]}_{name}"
                            save_file.write_bytes(content_bytes)
                            downloaded += 1
                            has_pdf = True
                            
                    # Mark as read so we don't re-download
                    if has_pdf:
                        await client.patch(
                            f"https://graph.microsoft.com/v1.0/users/{mailbox}/messages/{msg_id}",
                            headers=headers,
                            json={"isRead": True}
                        )
                        
        return downloaded
