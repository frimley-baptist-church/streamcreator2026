import os
import sys
import json
import pickle
import argparse
import subprocess
import urllib.request
import tempfile

from dotenv import load_dotenv

from datetime import datetime, timedelta, timezone

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

load_dotenv()
load_dotenv("/etc/streamcreator2026/config.env")  # Load system-wide env vars if present

NONPROFIT_CATEGORY_ID = "29"

channel_id = os.environ.get("CHANNEL_ID", None)
if not channel_id:
    print(
        "[Warning]: 'CHANNEL_ID' not found in environment variables. Token will be saved, but cannot be verified against a specific YouTube channel.",
        file=sys.stderr,
    )
    sys.exit(1)

atem_ip = os.environ.get("ATEM_IP", None)
if not atem_ip:
    print(
        "[Warning]: 'ATEM_IP' not found in environment variables. ATEM stream key routing will be skipped.",
        file=sys.stderr,
    )
    sys.exit(1)

broadcast_meta_url = os.environ.get("BROADCAST_META_URL", None)
if not broadcast_meta_url:
    print(
        "[Warning]: 'BROADCAST_META_URL' not found in environment variables. Broadcast metadata will be loaded from 'config/broadcast.json' if available.",
        file=sys.stderr,
    )
    sys.exit(1)

thumbnail_url = os.environ.get("THUMBNAIL_URL", None)
if not thumbnail_url:
    print(
        "[Warning]: 'THUMBNAIL_URL' not found in environment variables. Broadcast thumbnail upload will be skipped.",
        file=sys.stderr,
    )
    sys.exit(1)


SCOPES = ["https://www.googleapis.com/auth/youtube"]
ATEM_STREAMKEY_SCRIPT = os.path.join(os.path.dirname(__file__), "atem-streamkey")


def validate_broadcast_config(config):
    """Performs strict validation checks on the loaded configuration payload.
    Aborts execution by raising a ValueError if critical constraints are violated.
    """
    print("Validating configuration payload constraints...")

    # 1. Enforce critical structural fields needed for deployment
    required_fields = ["title", "start"]
    missing = [field for field in required_fields if not config.get(field)]
    if missing:
        raise ValueError(
            f"Missing required configuration properties: {', '.join(missing)}"
        )

    # 2. Strict Temporal Validation: Enforce that the start time is in the future
    start_time_raw = config.get("start")
    try:
        # Normalize the ISO 8601 string to a timezone-aware UTC datetime object
        # Stripping trailing 'Z' and replacing with '+00:00' ensures compatibility
        clean_ts = start_time_raw.rstrip("Z").split(".")[
            0
        ]  # strip fractional seconds if present
        scheduled_utc = datetime.fromisoformat(f"{clean_ts}+00:00")
    except Exception as parse_exc:
        raise ValueError(
            f"Invalid format for 'start_time' string: '{start_time_raw}'. Must be valid ISO 8601."
        ) from parse_exc

    now_utc = datetime.now(timezone.utc)

    # Check if the scheduled time has already passed
    print(f"scheduled_utc: {scheduled_utc.isoformat()}, now_utc: {now_utc.isoformat()}")
    if scheduled_utc <= now_utc:
        time_diff = now_utc - scheduled_utc
        raise ValueError(
            f"The scheduled start time '{start_time_raw}' is in the PAST "
            f"(Passed by {time_diff.days}d {time_diff.seconds // 3600}h {(time_diff.seconds % 3600) // 60}m ago). "
            f"Aborting execution to avoid a corrupted deployment."
        )

    print(" Configuration successfully passed validation checks.")


def get_youtube_client():
    """Handles standard OAuth2 authentication for the domain user."""
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


def upload_broadcast_thumbnail(youtube, broadcast_id, thumbnail_url, delegate_params):
    """Downloads a thumbnail from a URL and uploads it to the YouTube Live Broadcast."""
    if not thumbnail_url:
        return

    print(f"Downloading broadcast thumbnail from: {thumbnail_url}")
    try:
        with tempfile.NamedTemporaryFile(delete=False) as temp_thumb:
            temp_path = temp_thumb.name

            req = urllib.request.Request(
                thumbnail_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            )
            with urllib.request.urlopen(req) as response:
                temp_thumb.write(response.read())

        print("Uploading thumbnail to YouTube Live Broadcast...")
        media = MediaFileUpload(temp_path, mimetype="image/jpeg", resumable=True)

        thumbnail_params = {
            "videoId": broadcast_id,
            "media_body": media,
            **delegate_params,
        }

        youtube.thumbnails().set(**thumbnail_params).execute()
        print(" Successfully updated broadcast thumbnail.")

    except Exception as exc:
        print(f"[Warning]: Thumbnail upload failed: {exc}", file=sys.stderr)
    finally:
        if "temp_path" in locals() and os.path.exists(temp_path):
            os.remove(temp_path)


def purge_expired_youtube_streams(youtube, max_age_hours=24):
    """Deletes old, unbound RTMP stream keys to keep the YouTube Studio dashboard clean.
    Safeguards streams bound to current, upcoming, or active broadcasts.
    """
    print(f"\nScanning for old RTMP stream keys (older than {max_age_hours} hours)...")

    # 1. Gather all active, upcoming, and recently completed broadcasts to get protected stream IDs
    protected_stream_ids = set()
    try:
        broadcasts_request = youtube.liveBroadcasts().list(
            part="contentDetails",
            broadcastStatus="all",  # Pulls active, upcoming, and recent
            maxResults=50,
        )
        while broadcasts_request:
            broadcasts_response = broadcasts_request.execute()
            for item in broadcasts_response.get("items", []):
                stream_id = item.get("contentDetails", {}).get("boundStreamId")
                if stream_id:
                    protected_stream_ids.add(stream_id)

            broadcasts_request = youtube.liveBroadcasts().list_next(
                broadcasts_request, broadcasts_response
            )
    except Exception as e:
        print(
            f"[Warning]: Could not look up active broadcasts. Aborting cleanup for safety: {e}",
            file=sys.stderr,
        )
        return

    # 2. Iterate through all stream keys on the channel
    try:
        streams_request = youtube.liveStreams().list(
            part="snippet,id", mine=True, maxResults=50
        )

        now = datetime.now(timezone.utc)
        deleted_count = 0

        while streams_request:
            streams_response = streams_request.execute()
            for stream in streams_response.get("items", []):
                stream_id = stream.get("id")
                title = stream.get("snippet", {}).get("title", "")
                published_at_str = stream.get("snippet", {}).get("publishedAt")

                # Skip if it's currently bound to a broadcast
                if stream_id in protected_stream_ids:
                    continue

                if not published_at_str:
                    continue

                # Convert YouTube's ISO timestamp (e.g., "2026-05-24T12:00:00Z") to a timezone-aware datetime
                # Replacing 'Z' with '+00:00' makes it compatible with fromisoformat across modern Python versions
                published_at = datetime.fromisoformat(
                    published_at_str.replace("Z", "+00:00")
                )
                age = now - published_at

                # If the key is older than our threshold, vaporize it
                if age > timedelta(hours=max_age_hours):
                    print(
                        f" -> Deleting old stream: '{title}' (ID: {stream_id}, Age: {age.days}d {age.seconds//3600}h)"
                    )
                    try:
                        # youtube.liveStreams().delete(id=stream_id).execute()
                        deleted_count += 1
                    except Exception as delete_error:
                        print(
                            f"    [Warning]: Failed to delete stream {stream_id}: {delete_error}",
                            file=sys.stderr,
                        )

            streams_request = youtube.liveStreams().list_next(
                streams_request, streams_response
            )

        print(f"Clean-up complete. Removed {deleted_count} stale stream key(s).")

    except Exception as e:
        print(
            f"[Warning]: Failed to execute stream key clean-up loop: {e}",
            file=sys.stderr,
        )


def create_youtube_broadcast(youtube, config):
    """Creates the Live Broadcast shell, uploads an optional thumbnail,
    and creates an associated RTMP Stream.
    """
    thumbnail_url = config.get("thumbnail_url")

    print("Creating YouTube Live Broadcast container...")
    broadcast_body = {
        "snippet": {
            "title": config.get("title", "Automated Live Stream"),
            "description": config.get("description", ""),
            "scheduledStartTime": config.get("start"),
            "categoryId": NONPROFIT_CATEGORY_ID,
        },
        "status": {
            "privacyStatus": config.get("privacy", "public"),
            "selfDeclaredMadeForKids": False,
        },
        "contentDetails": {"enableAutoStart": True, "enableAutoStop": True},
    }

    broadcast_response = (
        youtube.liveBroadcasts()
        .insert(part="snippet,status,contentDetails", body=broadcast_body)
        .execute()
    )

    broadcast_id = broadcast_response["id"]
    print(f" Successfully created broadcast ID: {broadcast_id}")

    if thumbnail_url:
        upload_broadcast_thumbnail(youtube, broadcast_id, thumbnail_url, {})

    print("Creating underlying RTMP Stream ingestion channel...")

    # 1. Generate the yyyy-mm-dd hh:mm:ss timestamp (using local time here)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 2. Initial body payload (YouTube requires a snippet title on creation)
    stream_body = {
        "snippet": {"title": f"Stream {timestamp}"},
        "cdn": {
            "ingestionType": "rtmp",
            "frameRate": "variable",
            "resolution": "variable",
        },
    }

    stream_response = (
        youtube.liveStreams().insert(part="snippet,cdn", body=stream_body).execute()
    )

    stream_id = stream_response["id"]
    active_stream_key = stream_response["cdn"]["ingestionInfo"]["streamName"]

    # 3. Update the stream title to include both the timestamp and the newly acquired stream ID
    print(f"Updating stream title with timestamp and ID: {broadcast_id}...")
    updated_title = f"{timestamp} ({broadcast_id})"

    update_body = {
        "id": stream_id,
        "snippet": {"title": updated_title},
        "cdn": stream_response[
            "cdn"
        ],  # The API requires CDN info to remain present during updates
    }

    try:
        youtube.liveStreams().update(part="snippet,cdn", body=update_body).execute()
        print(" Successfully named the stream channel.")
    except Exception as e:
        # We catch this so a cosmetic title update failure doesn't crash your entire deployment
        print(
            f"[Warning]: Failed to update stream name to include ID: {e}",
            file=sys.stderr,
        )

    print("Binding ingestion stream to the broadcast...")
    youtube.liveBroadcasts().bind(
        id=broadcast_id, part="id,contentDetails", streamId=stream_id
    ).execute()

    return active_stream_key, broadcast_id


def run_atem_helper(command, error_context):
    """Runs an external ATEM helper script and returns stripped stdout."""
    print(f"Running ATEM helper command: {' '.join(command)}")
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
    except OSError as exc:
        raise RuntimeError(f"{error_context}\n{exc}") from exc

    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        if details:
            raise RuntimeError(f"{error_context}\n{details}")
        raise RuntimeError(error_context)

    return result.stdout.strip()


def get_atem_stream_state(ip_address=None):
    """Reads the current ATEM streaming service state via the local helper script."""
    output = run_atem_helper(
        [ATEM_STREAMKEY_SCRIPT],
        f"[Error]: Failed to read the ATEM stream key with {ATEM_STREAMKEY_SCRIPT}.",
    )

    try:
        data = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"[Error]: Failed to parse ATEM helper output as JSON.\n{output}"
        ) from exc

    return {
        "serviceName": data.get("serviceName", ""),
        "url": data.get("url", ""),
        "key": data.get("key", ""),
    }


def get_atem_stream_key(ip_address=None):
    """Reads the current ATEM stream key via the local helper script."""
    return get_atem_stream_state(ip_address=ip_address)["key"]


def set_atem_stream_key(stream_key, ip_address=None):
    """Writes a new ATEM stream key via the local helper script."""
    output = run_atem_helper(
        [ATEM_STREAMKEY_SCRIPT, "set", stream_key],
        f"[Error]: Failed to set the ATEM stream key with {ATEM_STREAMKEY_SCRIPT}.",
    )

    try:
        data = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"[Error]: Failed to parse ATEM helper output as JSON.\n{output}"
        ) from exc

    return {
        "serviceName": data.get("serviceName", ""),
        "url": data.get("url", ""),
        "key": data.get("key", ""),
    }


def configure_atem_switcher(ip_address=None, stream_key=None, validate_only=False):
    """Reads, writes, and validates the ATEM stream key via the local helper."""

    print("Fetching current ATEM state via external helper...")
    current_state = get_atem_stream_state(ip_address=ip_address)
    current_key = current_state["key"]

    if validate_only:
        return current_state

    if stream_key is not None:
        if current_key == stream_key:
            print(
                " ATEM Switcher already has the correct stream key. No update needed."
            )
            return current_state

        print(" Applying new ATEM stream key via external helper...")
        new_state = set_atem_stream_key(stream_key, ip_address=ip_address)
        new_key = new_state["key"]
        if new_key != stream_key:
            raise RuntimeError(
                f"[Error]: ATEM validation failed! Expected '{stream_key}', but helper reports '{new_key}'"
            )

        print(" ATEM Switcher updated and verified successfully.")
        return new_state

    return current_state


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Automate YouTube Live and ATEM Mini stream key routing."
    )

    parser.add_argument(
        "--auth-only",
        action="store_true",
        help="Only execute the one-time browser auth loop.",
    )

    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Purge old, unbound RTMP stream keys from the account.",
    )

    args = parser.parse_args()

    # Mode 1: Bootstrap Token on your Laptop
    if args.auth_only:
        yt_client = get_youtube_client()

        # Validation output using the channel ID from broadcast.json if available
        try:
            channels_response = (
                yt_client.channels().list(part="snippet", id=channel_id).execute()
            )

            if channels_response.get("items"):
                channel_name = channels_response["items"][0]["snippet"]["title"]
                print(
                    f"\n[Verification]: Token successfully validated for target channel: '{channel_name}'"
                )
            else:
                print(
                    f"\n[Verification Warning]: Channel ID '{channel_id}' found no matching public channel."
                )
        except Exception as e:
            print(
                f"\n[Verification Warning]: Could not pull target channel metadata directly: {e}"
            )

        sys.exit(0)

    # Mode 2: Clean-up Routine
    if args.cleanup:
        yt_client = get_youtube_client()
        purge_expired_youtube_streams(yt_client, max_age_hours=24)
        sys.exit(0)

    # Load configuration if it exists - mandatory for run
    config_data = {}

    try:
        if broadcast_meta_url:
            print(
                f"Attempting to load broadcast metadata from URL: {broadcast_meta_url}"
            )
            with urllib.request.urlopen(broadcast_meta_url) as response:
                if response.status == 200:
                    config_data = json.loads(response.read())
                    print(" Successfully loaded broadcast metadata from URL.")
                else:
                    print(
                        f"[Warning]: Received non-200 response ({response.status}) when fetching broadcast metadata. Falling back to local file if available.",
                        file=sys.stderr,
                    )
    except Exception as e:
        print(
            f"[Warning]: Failed to load broadcast metadata from URL: {e}. Falling back to local file if available.",
            file=sys.stderr,
        )

    if not config_data:
        if os.path.exists("config/broadcast.json"):
            with open("config/broadcast.json", "r") as f:
                config_data = json.load(f)
            print(
                " Successfully loaded broadcast metadata from 'config/broadcast.json'."
            )
        else:
            print(
                "[Notice]: No broadcast metadata found from URL or local file.",
                file=sys.stderr,
            )
            sys.exit(1)

    config_data["atem_ip"] = atem_ip
    config_data["thumbnail_url"] = thumbnail_url
    config_data["channel_id"] = channel_id

    try:
        validate_broadcast_config(config_data)

        yt_client = get_youtube_client()
        active_stream_key, broadcast_id = create_youtube_broadcast(
            yt_client, config_data
        )

        atem_result = configure_atem_switcher(
            ip_address=config_data.get("atem_ip"),
            stream_key=active_stream_key,
        )

        print("\n" + "=" * 50)
        print("DEPLOYMENT SUCCESSFUL - LIVE STREAM READY")
        print("=" * 50)
        print(f"YouTube Live URL : https://youtu.be/{broadcast_id}")
        print(
            f"YouTube Studio   : https://studio.youtube.com/video/{broadcast_id}/livestreaming"
        )
        print("-" * 50)
        print("ATEM Switcher State:")
        print(json.dumps(atem_result, indent=4))
        print("=" * 50)

    except Exception as e:
        print(f"\n[Runtime Failure]: {e}", file=sys.stderr)
        sys.exit(1)
