#!/usr/bin/env python3
import os
import sys
import pickle
import json

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]


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


def report_broadcasts(youtube):
    """Fetches and prints current (active) and pending (upcoming) broadcasts.
    Returns a set of bound stream IDs for targeted stream inspection.
    """
    print("\n" + "=" * 60)
    print("ACTIVE & PENDING BROADCASTS")
    print("=" * 60)

    target_statuses = ["active", "upcoming"]
    found_count = 0
    bound_stream_ids = set()

    for status in target_statuses:
        try:
            request = youtube.liveBroadcasts().list(
                part="snippet,status,contentDetails",
                broadcastStatus=status,
                maxResults=50,
            )
            while request:
                response = request.execute()
                items = response.get("items", [])
                for item in items:
                    found_count += 1
                    b_id = item["id"]
                    snippet = item.get("snippet", {})
                    b_status = item.get("status", {})
                    content = item.get("contentDetails", {})
                    stream_id = content.get("boundStreamId")

                    if stream_id:
                        bound_stream_ids.add(stream_id)

                    print(f"[{status.upper()}]")
                    print(f"  Broadcast ID : {b_id}")
                    print(f"  Title        : {snippet.get('title')}")
                    print(f"  Scheduled    : {snippet.get('scheduledStartTime', 'N/A')}")
                    print(f"  LifeCycle    : {b_status.get('lifeCycleStatus')}")
                    print(f"  Privacy      : {b_status.get('privacyStatus')}")
                    print(f"  Bound Stream : {stream_id or 'None'}")
                    print("-" * 40)

                request = youtube.liveBroadcasts().list_next(request, response)
        except Exception as e:
            print(f"[Error querying {status} broadcasts]: {e}", file=sys.stderr)

    if found_count == 0:
        print("No active or pending broadcasts found.")

    return bound_stream_ids


def report_streams_and_formats(youtube, target_stream_ids):
    """Fetches and prints stream configurations. Uses targeted ID lookup
    if the global `mine=True` endpoint triggers a Google backend 500 error.
    """
    print("\n" + "=" * 60)
    print("LIVE STREAM & CDN FORMAT OPTIONS")
    print("=" * 60)

    items = []

    # Attempt 1: Try global mine=True query
    try:
        request = youtube.liveStreams().list(
            part="snippet,cdn,status", mine=True, maxResults=50
        )
        while request:
            response = request.execute()
            items.extend(response.get("items", []))
            request = youtube.liveStreams().list_next(request, response)
    except HttpError as he:
        if he.resp.status == 500:
            print("[Notice]: YouTube API returned an Internal Error (500) on global stream listing.")
            print("[Notice]: Falling back to querying specific bound streams from active/pending broadcasts...")
        else:
            print(f"[HttpError querying live streams]: {he}", file=sys.stderr)
            return
    except Exception as e:
        print(f"[Error querying live streams]: {e}", file=sys.stderr)
        return

    # Attempt 2: Fallback to querying specific bound stream IDs if global listing failed or was empty
    if not items and target_stream_ids:
        try:
            response = youtube.liveStreams().list(
                part="snippet,cdn,status",
                id=",".join(target_stream_ids)
            ).execute()
            items = response.get("items", [])
        except Exception as e:
            print(f"[Error querying targeted streams]: {e}", file=sys.stderr)
            return

    if not items:
        print("No accessible live streams found.")
        return

    for stream in items:
        s_id = stream.get("id")
        snippet = stream.get("snippet", {})
        cdn = stream.get("cdn", {})
        status = stream.get("status", {})

        print(f"Stream ID     : {s_id}")
        print(f"Title         : {snippet.get('title')}")
        print(f"Stream Status : {status.get('streamStatus', 'N/A')}")
        print("CDN Configuration:")
        print(f"  - Ingestion Type : {cdn.get('ingestionType')}")
        print(f"  - Resolution     : {cdn.get('resolution')}")
        print(f"  - Frame Rate     : {cdn.get('frameRate')}")
        print(f"  - Stream Type    : {cdn.get('streamType')}")

        ingestion_info = cdn.get("ingestionInfo")
        if ingestion_info:
            print(f"  - Ingest URL     : {ingestion_info.get('ingestionAddress')}")
            print(f"  - Backup URL     : {ingestion_info.get('backupIngestionAddress')}")
        print("-" * 40)


if __name__ == "__main__":
    print("Initializing YouTube API client inspection...")
    yt_client = get_youtube_client()

    bound_streams = report_broadcasts(yt_client)
    report_streams_and_formats(yt_client, bound_streams)

    print("\nInspection complete.")
