"""Gmail API helpers for the creator outreach agent.

This module deliberately reuses the existing creator Gmail OAuth credential and
refresh service. It never selects credentials from frontend-supplied user IDs and
never returns OAuth tokens to callers.
"""
from __future__ import annotations

import base64
import html
import logging
from email.message import EmailMessage
from typing import Any

import requests
from django.utils import timezone

from creators.models import GmailOAuthCredential
from creators.services.gmail_oauth import (
    GmailOAuthError,
    get_gmail_connection_status as oauth_connection_status,
    get_valid_gmail_access_token as oauth_valid_access_token,
)

logger = logging.getLogger(__name__)
GMAIL_API_ROOT = "https://gmail.googleapis.com/gmail/v1/users/me"
REQUEST_TIMEOUT_SECONDS = 20


class MissingGmailConnection(Exception):
    pass


class GmailNeedsReauth(Exception):
    pass


class GmailApiError(Exception):
    pass


class GmailPermissionError(GmailApiError):
    pass


def get_connection_status(user) -> dict[str, Any]:
    return oauth_connection_status(user)


def get_valid_access_token(user) -> str:
    try:
        return oauth_valid_access_token(user)
    except GmailOAuthError as exc:
        status = get_connection_status(user).get("status")
        if status in {GmailOAuthCredential.STATUS_NEEDS_REAUTH, GmailOAuthCredential.STATUS_REVOKED}:
            raise GmailNeedsReauth(str(exc)) from exc
        raise MissingGmailConnection(str(exc)) from exc


def _headers(user) -> dict[str, str]:
    return {"Authorization": f"Bearer {get_valid_access_token(user)}"}


def _request(user, method: str, path: str, **kwargs) -> dict[str, Any]:
    url = f"{GMAIL_API_ROOT}{path}"
    kwargs.setdefault("timeout", REQUEST_TIMEOUT_SECONDS)
    try:
        response = requests.request(method, url, headers=_headers(user), **kwargs)
    except requests.RequestException as exc:
        raise GmailApiError("Gmail API request failed. Please try again.") from exc
    if response.status_code in {401, 403}:
        raise GmailPermissionError("Gmail authorization failed. Please reconnect Gmail.")
    if response.status_code >= 400:
        raise GmailApiError("Gmail API request failed. Please try again.")
    if response.status_code == 204 or not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise GmailApiError("Gmail API returned an invalid response.") from exc


def _message_payload(to: str, subject: str, body: str, thread_id: str | None = None,
                     in_reply_to: str | None = None, references: str | None = None) -> dict[str, Any]:
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if references:
        message["References"] = references
    message.set_content(body or "")
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    payload: dict[str, Any] = {"raw": raw}
    if thread_id:
        payload["threadId"] = thread_id
    return payload


def _header(message: dict[str, Any], name: str) -> str:
    for header in message.get("payload", {}).get("headers", []) or []:
        if str(header.get("name", "")).lower() == name.lower():
            return header.get("value", "") or ""
    return ""


def _body_from_part(part: dict[str, Any]) -> str:
    if not isinstance(part, dict):
        return ""
    data = (part.get("body") or {}).get("data") or ""
    if data:
        try:
            decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")
            return html.unescape(decoded)
        except Exception:
            return ""
    for child in part.get("parts", []) or []:
        text = _body_from_part(child)
        if text:
            return text
    return ""


def _normalize_message(message: dict[str, Any]) -> dict[str, Any]:
    internal_date = message.get("internalDate")
    sent_at = ""
    if internal_date:
        try:
            sent_at = timezone.datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc).isoformat()
        except Exception:
            sent_at = ""
    return {
        "id": message.get("id", ""),
        "thread_id": message.get("threadId", ""),
        "from": _header(message, "From"),
        "to": _header(message, "To"),
        "subject": _header(message, "Subject"),
        "date": _header(message, "Date"),
        "sent_at": sent_at,
        "message_id": _header(message, "Message-ID"),
        "references": _header(message, "References"),
        "snippet": message.get("snippet", ""),
        "body": _body_from_part(message.get("payload", {}) or ""),
    }


def search_threads(user, query: str, max_results: int = 10) -> list[dict[str, Any]]:
    max_results = max(1, min(int(max_results or 10), 25))
    payload = _request(user, "GET", "/threads", params={"q": query or "", "maxResults": max_results})
    threads = []
    for item in payload.get("threads", []) or []:
        thread_id = item.get("id")
        if not thread_id:
            continue
        detail = _request(user, "GET", f"/threads/{thread_id}", params={"format": "metadata"})
        messages = detail.get("messages", []) or []
        last = messages[-1] if messages else {}
        threads.append({
            "id": thread_id,
            "history_id": detail.get("historyId", ""),
            "message_count": len(messages),
            "subject": _header(last, "Subject"),
            "from": _header(last, "From"),
            "snippet": last.get("snippet", detail.get("snippet", "")),
        })
    return threads


def read_thread(user, thread_id: str) -> dict[str, Any]:
    payload = _request(user, "GET", f"/threads/{thread_id}", params={"format": "full"})
    messages = [_normalize_message(message) for message in payload.get("messages", []) or []]
    return {"id": payload.get("id", thread_id), "history_id": payload.get("historyId", ""), "messages": messages}


def create_draft(user, to: str, subject: str, body: str, thread_id: str | None = None,
                 in_reply_to_message_id: str | None = None) -> dict[str, Any]:
    references = in_reply_to_message_id or None
    payload = {"message": _message_payload(to, subject, body, thread_id, in_reply_to_message_id, references)}
    result = _request(user, "POST", "/drafts", json=payload)
    message = result.get("message", {}) or {}
    return {"draft_id": result.get("id", ""), "message_id": message.get("id", ""), "thread_id": message.get("threadId", thread_id or "")}


def update_draft(user, draft_id: str, to: str, subject: str, body: str) -> dict[str, Any]:
    result = _request(user, "PUT", f"/drafts/{draft_id}", json={"message": _message_payload(to, subject, body)})
    message = result.get("message", {}) or {}
    return {"draft_id": result.get("id", draft_id), "message_id": message.get("id", ""), "thread_id": message.get("threadId", "")}


def send_draft(user, draft_id: str) -> dict[str, Any]:
    result = _request(user, "POST", "/drafts/send", json={"id": draft_id})
    return {"message_id": result.get("id", ""), "thread_id": result.get("threadId", "")}


def send_email(user, to: str, subject: str, body: str) -> dict[str, Any]:
    result = _request(user, "POST", "/messages/send", json=_message_payload(to, subject, body))
    return {"message_id": result.get("id", ""), "thread_id": result.get("threadId", "")}


def reply_to_thread(user, thread_id: str, to: str, subject: str, body: str) -> dict[str, Any]:
    thread = read_thread(user, thread_id)
    last = (thread.get("messages") or [{}])[-1]
    in_reply_to = last.get("message_id") or ""
    references = last.get("references") or in_reply_to
    result = _request(user, "POST", "/messages/send", json=_message_payload(to, subject, body, thread_id, in_reply_to, references))
    return {"message_id": result.get("id", ""), "thread_id": result.get("threadId", thread_id)}


def archive_thread(user, thread_id: str) -> dict[str, Any]:
    return _request(user, "POST", f"/threads/{thread_id}/modify", json={"removeLabelIds": ["INBOX"]})
