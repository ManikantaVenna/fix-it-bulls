"""Gmail API operations. A send request is never automatically retried."""

import base64
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from urllib.parse import quote

import requests
from flask import current_app
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from schemas import MailEvent

GMAIL_SCOPES = (
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
)
BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


class ProviderError(RuntimeError):
    """Gmail rejected the request or access could not be established."""


class DeliveryUncertain(ProviderError):
    """Gmail may have received the message."""


def _access_token(refresh_token):
    config = current_app.config
    credentials = Credentials(
        token=None, refresh_token=refresh_token, client_id=config["GOOGLE_CLIENT_ID"],
        client_secret=config["GOOGLE_CLIENT_SECRET"], token_uri="https://oauth2.googleapis.com/token",
        scopes=GMAIL_SCOPES,
    )
    transport = Request()
    try:
        credentials.refresh(lambda *args, **kwargs: transport(*args, **{**kwargs, "timeout": 12}))
    except GoogleAuthError as error:
        raise ProviderError from error
    if not credentials.token:
        raise ProviderError
    return credentials.token


def build_message(sender, recipient, subject, body, photo, rfc_message_id):
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message["Message-ID"] = rfc_message_id
    message.set_content(body)
    if photo is not None:
        message.add_attachment(photo, maintype="image", subtype="jpeg", filename="report-photo.jpg")
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


def send_message(refresh_token, raw):
    token = _access_token(refresh_token)
    try:
        response = requests.post(
            f"{BASE}/messages/send", headers={"Authorization": f"Bearer {token}"},
            json={"raw": raw}, timeout=(5, 18), allow_redirects=False,
        )
    except requests.RequestException as error:
        raise DeliveryUncertain from error
    if response.status_code == 408 or response.status_code >= 500:
        raise DeliveryUncertain
    if not 200 <= response.status_code < 300:
        raise ProviderError
    try:
        result = response.json()
    except ValueError as error:
        raise DeliveryUncertain from error
    if not isinstance(result, dict) or not all(isinstance(result.get(key), str) and result[key] for key in ("id", "threadId")):
        raise DeliveryUncertain
    return result


def get_thread(refresh_token, thread_id):
    token = _access_token(refresh_token)
    try:
        response = requests.get(
            f"{BASE}/threads/{quote(thread_id, safe='')}", headers={"Authorization": f"Bearer {token}"},
            params={"format": "full"}, timeout=(5, 18), allow_redirects=False,
        )
        if response.status_code != 200:
            raise ProviderError
        result = response.json()
    except (requests.RequestException, ValueError) as error:
        raise ProviderError from error
    if not isinstance(result, dict) or result.get("id") != thread_id:
        raise ProviderError
    return result


def _plain_text(part):
    # Read only inline text, ignoring attachments, HTML and embedded messages.
    if part.get("filename") or part.get("mimeType") == "message/rfc822":
        return ""
    if part.get("mimeType") == "text/plain":
        data = part.get("body", {}).get("data", "")[:28_000]
        try:
            return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")[:20_000]
        except ValueError:
            return ""
    for child in part.get("parts", []):
        body = _plain_text(child)
        if body:
            return body
    return ""


def incoming_events(thread, sent_message_id):
    messages = thread.get("messages", [])
    original = next((message for message in messages if message.get("id") == sent_message_id), None)
    if original is None:
        raise ProviderError
    try:
        created = int(original["internalDate"])
        replies = {}
        for message in messages:
            if message["id"] == sent_message_id or set(message.get("labelIds", [])) & {"SENT", "DRAFT", "TRASH", "SPAM"}:
                continue
            timestamp = int(message["internalDate"])
            if timestamp < created:
                continue
            payload = message.get("payload", {})
            header_values = {item["name"].lower(): item["value"] for item in payload.get("headers", [])}
            replies[message["id"]] = MailEvent(
                provider_message_id=message["id"], direction="incoming",
                sender=header_values.get("from", "Unknown sender")[:320],
                received_at=datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc),
                safe_text=_plain_text(payload) or "This reply has no plain-text preview. Open it in Gmail.",
            )
    except (KeyError, ValueError, TypeError, OverflowError, OSError) as error:
        raise ProviderError from error
    return sorted(replies.values(), key=lambda event: event.received_at)
