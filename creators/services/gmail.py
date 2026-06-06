"""Gmail API service layer for creator outreach.

All token access stays server-side. Callers must scope every operation to the
request.user; this module never accepts a frontend-supplied user id.
"""

from __future__ import annotations

import base64
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any

import requests
from django.core.validators import validate_email
from django.core.exceptions import ValidationError

from creators.models import GmailOAuthCredential
from creators.services.gmail_oauth import (
    GmailOAuthError,
    build_gmail_authorization_url,
    exchange_gmail_callback,
    get_gmail_connection_status,
    get_valid_gmail_access_token,
    refresh_gmail_token_if_needed,
    revoke_gmail_connection,
)

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
REQUEST_TIMEOUT_SECONDS = 20


class MissingGmailConnection(GmailOAuthError):
    default_message = "Connect Gmail before using outreach email actions."


class GmailNeedsReauth(GmailOAuthError):
    default_message = "Reconnect Gmail before using outreach email actions."


class GmailApiError(GmailOAuthError):
    default_message = "Gmail could not complete that action. Please try again."


class GmailPermissionError(GmailApiError):
    default_message = "Badger does not have permission to complete that Gmail action."


def get_connection_status(user) -> dict[str, Any]:
    return get_gmail_connection_status(user)


def build_authorization_url(user, request) -> str:
    return build_gmail_authorization_url(request)


def exchange_callback_code(user, request) -> GmailOAuthCredential:
    return exchange_gmail_callback(request)


def refresh_if_needed(user) -> GmailOAuthCredential:
    credential = getattr(user, "gmail_oauth_credential", None)
    if not credential:
        raise MissingGmailConnection()
    credential = refresh_gmail_token_if_needed(credential)
    if credential.status == GmailOAuthCredential.STATUS_NEEDS_REAUTH:
        raise GmailNeedsReauth()
    return credential


def revoke(user) -> None:
    revoke_gmail_connection(user)


def _access_token(user) -> str:
    try:
        return get_valid_gmail_access_token(user)
    except GmailOAuthError as exc:
        status = get_gmail_connection_status(user).get("status")
        if status in {GmailOAuthCredential.STATUS_NEEDS_REAUTH, GmailOAuthCredential.STATUS_REVOKED}:
            raise GmailNeedsReauth(exc.user_message) from exc
        raise MissingGmailConnection(exc.user_message) from exc


def _headers(user) -> dict[str, str]:
    return {"Authorization": f"Bearer {_access_token(user)}", "Content-Type": "application/json"}


def _request(user, method: str, path: str, **kwargs) -> dict[str, Any]:
    url = f"{GMAIL_API_BASE}{path}"
    try:
        response = requests.request(method, url, headers=_headers(user), timeout=REQUEST_TIMEOUT_SECONDS, **kwargs)
    except requests.RequestException as exc:
        raise GmailApiError() from exc
    if response.status_code in {401, 403}:
        raise GmailPermissionError()
    if response.status_code >= 400:
        raise GmailApiError()
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise GmailApiError("Gmail returned an invalid response.") from exc


def _message_raw(to: str, subject: str, body: str, *, thread_headers: dict[str, str] | None = None) -> str:
    try:
        validate_email(to)
    except ValidationError as exc:
        raise GmailApiError("Enter a valid recipient email address.") from exc
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject or ""
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid()
    for key, value in (thread_headers or {}).items():
        if value:
            message[key] = value
    message.set_content(body or "")
    raw_bytes = message.as_bytes()
    return base64.urlsafe_b64encode(raw_bytes).decode("ascii")


def _header_map(payload: dict[str, Any]) -> dict[str, str]:
    headers = payload.get("headers") or []
    return {str(item.get("name", "")).lower(): str(item.get("value", "")) for item in headers if isinstance(item, dict)}


def _decode_part_body(part: dict[str, Any]) -> str:
    data = ((part.get("body") or {}).get("data") or "").strip()
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _plain_text_from_payload(payload: dict[str, Any]) -> str:
    if not payload:
        return ""
    if payload.get("mimeType") == "text/plain":
        return _decode_part_body(payload)
    parts = payload.get("parts") or []
    for part in parts:
        if part.get("mimeType") == "text/plain":
            return _decode_part_body(part)
    for part in parts:
        text = _plain_text_from_payload(part)
        if text:
            return text
    return _decode_part_body(payload)


def _normalize_message(message: dict[str, Any]) -> dict[str, Any]:
    payload = message.get("payload") or {}
    headers = _header_map(payload)
    return {
        "id": message.get("id", ""),
        "thread_id": message.get("threadId", ""),
        "snippet": message.get("snippet", ""),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "message_id": headers.get("message-id", ""),
        "references": headers.get("references", ""),
        "in_reply_to": headers.get("in-reply-to", ""),
        "body": _plain_text_from_payload(payload),
    }


def _normalize_thread(thread: dict[str, Any]) -> dict[str, Any]:
    messages = [_normalize_message(message) for message in thread.get("messages", [])]
    return {
        "id": thread.get("id", ""),
        "history_id": thread.get("historyId", ""),
        "messages": messages,
        "subject": messages[-1].get("subject", "") if messages else "",
        "snippet": messages[-1].get("snippet", "") if messages else "",
        "last_message_id": messages[-1].get("message_id", "") if messages else "",
        "references": messages[-1].get("references", "") if messages else "",
    }


def list_recent_messages(user, max_results: int = 10) -> list[dict[str, Any]]:
    payload = _request(user, "GET", "/messages", params={"maxResults": max_results})
    messages = []
    for item in payload.get("messages", []):
        detail = _request(user, "GET", f"/messages/{item['id']}", params={"format": "full"})
        messages.append(_normalize_message(detail))
    return messages


def search_threads(user, query: str, max_results: int = 10) -> list[dict[str, Any]]:
    payload = _request(user, "GET", "/threads", params={"q": query or "", "maxResults": max_results})
    threads = []
    for item in payload.get("threads", []):
        detail = _request(user, "GET", f"/threads/{item['id']}", params={"format": "metadata"})
        threads.append(_normalize_thread(detail))
    return threads


def read_thread(user, thread_id: str) -> dict[str, Any]:
    if not thread_id:
        raise GmailApiError("Thread id is required.")
    detail = _request(user, "GET", f"/threads/{thread_id}", params={"format": "full"})
    return _normalize_thread(detail)


def create_draft(user, to: str, subject: str, body: str, thread_id: str | None = None, in_reply_to_message_id: str | None = None) -> dict[str, Any]:
    headers = {}
    if in_reply_to_message_id:
        headers = {"In-Reply-To": in_reply_to_message_id, "References": in_reply_to_message_id}
    message = {"raw": _message_raw(to, subject, body, thread_headers=headers)}
    if thread_id:
        message["threadId"] = thread_id
    return _request(user, "POST", "/drafts", json={"message": message})


def update_draft(user, draft_id: str, to: str, subject: str, body: str) -> dict[str, Any]:
    if not draft_id:
        raise GmailApiError("Draft id is required.")
    return _request(user, "PUT", f"/drafts/{draft_id}", json={"message": {"raw": _message_raw(to, subject, body)}})


def send_draft(user, draft_id: str) -> dict[str, Any]:
    if not draft_id:
        raise GmailApiError("Draft id is required.")
    return _request(user, "POST", "/drafts/send", json={"id": draft_id})


def send_email(user, to: str, subject: str, body: str) -> dict[str, Any]:
    return _request(user, "POST", "/messages/send", json={"raw": _message_raw(to, subject, body)})


def reply_to_thread(user, thread_id: str, to: str, subject: str, body: str) -> dict[str, Any]:
    thread = read_thread(user, thread_id)
    in_reply_to = thread.get("last_message_id", "")
    headers = {"In-Reply-To": in_reply_to, "References": thread.get("references") or in_reply_to}
    message = {"raw": _message_raw(to, subject, body, thread_headers=headers), "threadId": thread_id}
    return _request(user, "POST", "/messages/send", json=message)


def archive_thread(user, thread_id: str) -> dict[str, Any]:
    return _request(user, "POST", f"/threads/{thread_id}/modify", json={"removeLabelIds": ["INBOX"]})
