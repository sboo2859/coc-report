import json
import os
import sys
from datetime import datetime
from urllib.parse import quote


DEFAULT_CLAN_TAG = "#22YY2LPV2"
DEFAULT_WAR_DIR = "data/wars"


def get_api_token():
    return os.environ.get("COC_API_TOKEN", "").strip()


def get_clan_tag():
    return os.environ.get("COC_CLAN_TAG", DEFAULT_CLAN_TAG).strip()


def fetch_current_war(api_token=None, clan_tag=None, timeout=20):
    api_token = api_token or get_api_token()
    clan_tag = clan_tag or get_clan_tag()

    if not api_token:
        raise RuntimeError(
            'Missing COC_API_TOKEN environment variable. Example: export COC_API_TOKEN="your Clash API token"'
        )

    import requests

    encoded_clan_tag = quote(clan_tag, safe="")
    url = f"https://api.clashofclans.com/v1/clans/{encoded_clan_tag}/currentwar"
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Accept": "application/json",
    }

    response = requests.get(url, headers=headers, timeout=timeout)
    if response.status_code != 200:
        raise RuntimeError(f"Clash API returned {response.status_code}: {response.text}")

    return response.json(), response.status_code


def print_war_participation(data):
    print("\n=== War Participation ===")

    members = data.get("clan", {}).get("members", [])
    for member in members:
        name = member.get("name", "Unknown")
        attacks = member.get("attacks", [])
        attacks_used = len(attacks)

        if attacks_used == 0:
            print(f"MISSED: {name}")
        else:
            print(f"{name}: {attacks_used} attacks")


def save_war_snapshot(data, output_dir=DEFAULT_WAR_DIR, prefix="war"):
    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    filename = os.path.join(output_dir, f"{prefix}_{timestamp}.json")

    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

    return filename


def main():
    try:
        data, status_code = fetch_current_war()
    except RuntimeError as exc:
        print(exc)
        sys.exit(1)

    print("Status Code:", status_code)
    print_war_participation(data)

    filename = save_war_snapshot(data)
    print(f"\nSaved war snapshot to {filename}")


if __name__ == "__main__":
    main()
