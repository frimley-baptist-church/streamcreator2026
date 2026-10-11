#!/usr/bin/env python3
import os
import sys
import pickle
from datetime import datetime, timedelta, timezone

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/youtube"]


def get_youtube_client():
    """Handles standard OAuth2 authentication, reusing token.pickle."""
    creds = None
    if os.path.exists("token.pickle"):
        with open("token.pickle", "rb") as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing existing token in background...")
            creds.refresh(Request())
        else:
            print("Starting authentication loop...")
            if not os.path.exists("client_secrets.json"):
                print("[Error]: 'client_secrets.json' is missing.", file=sys.stderr)
                sys.exit(1)

            flow = InstalledAppFlow.from_client_secrets_file(
                client_secrets_file="client_secrets.json", scopes=SCOPES
            )
            creds = flow.run_local_server(
                host="127.0.0.1",
                port=0,
                prompt="consent",
                success_message="Authentication successful! You can close this tab.",
            )

        with open("token.pickle", "wb") as token:
            pickle.dump(creds, token)
        print("Token successfully saved to 'token.pickle'.")

    return build("youtube", "v3", credentials=creds)


def purge_inactive_streams(youtube, max_age_hours=24):
    """Scans for inactive, unbound RTMP streams older than max_age_hours and deletes them."""
    print(f"\nScanning for inactive RTMP streams older than {max_age_hours} hours...")

    # 1. Gather all protected stream IDs currently bound to active or upcoming broadcasts
    protected_stream_ids = set()
    try:
        for status in ["active", "upcoming"]:
            request = youtube.liveBroadcasts().list(
                part="contentDetails",
                broadcastStatus=status,
                maxResults=50,
            )
            while request:
                response = request.execute()
                for item in response.get("items", []):
                    stream_id = item.get("contentDetails", {}).get("boundStreamId")
                    if stream_id:
                        protected_stream_ids.add(stream_id)
                request = youtube.liveBroadcasts().list_next(request, response)
    except Exception as e:
        print(f"[Warning]: Could not verify active broadcasts. Aborting cleanup for safety: {e}", file=sys.stderr)
        return

    # 2. List all streams on the channel
    try:
        streams_request = youtube.liveStreams().list(
            part="snippet,status,id", mine=True, maxResults=50
        )

        now = datetime.now(timezone.utc)
        deleted_count = 0
        items_to_check = []

        while streams_request:
            streams_response = streams_request.execute()
            items_to_check.extend(streams_response.get("items", []))
            streams_request = youtube.liveStreams().list_next(streams_request, streams_response)

        if not items_to_check:
            print("No live streams found on the account.")
            return

        for stream in items_to_check:
            stream_id = stream.get("id")
            title = stream.get("snippet", {}).get("title", "")
            published_at_str = stream.get("snippet", {}).get("publishedAt")
            status = stream.get("status", {}).get("streamStatus", "")

            # Safety check 1: Never delete if currently bound to a broadcast
            if stream_id in protected_stream_ids:
                print(f" [Skipped - Protected/Bound]: '{title}' ({stream_id})")
                continue

            # Safety check 2: Only target inactive streams
            if status != "inactive":
                print(f" [Skipped - Active/Ready]: '{title}' ({stream_id}) [Status: {status}]")
                continue

            if not published_at_str:
                continue

            # Parse age
            published_at = datetime.fromisoformat(published_at_str.replace("Z", "+00:00"))
            age = now - published_at

            # If older than threshold, delete it
            if age > timedelta(hours=max_age_hours):
                print(f" -> Deleting stale inactive stream: '{title}' (ID: {stream_id}, Age: {age.days}d {age.seconds//3600}h)")
                try:
                    youtube.liveStreams().delete(id=stream_id).execute()
                    deleted_count += 1
                except Exception as delete_error:
                    print(f"    [Warning]: Failed to delete stream {stream_id}: {delete_error}", file=sys.stderr)
            else:
                print(f" [Skipped - Too Recent]: '{title}' (Age: {age.seconds // 3600}h)")

        print(f"\nCleanup complete. Successfully removed {deleted_count} stale stream key(s).")

    except HttpError as he:
        print(f"[HttpError querying streams]: {he}", file=sys.stderr)
    except Exception as e:
        print(f"[Error executing stream cleanup]: {e}", file=sys.stderr)


if __name__ == "__main__":
    print("Initializing YouTube API client for stream purge...")
    yt_client = get_youtube_client()

    # Run purge targeting streams older than 24 hours
    purge_inactive_streams(yt_client, max_age_hours=24)
