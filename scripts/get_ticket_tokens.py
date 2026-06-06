from __future__ import annotations

import argparse
import json
import sys
from urllib import error as url_error
from urllib import request as url_request


DEFAULT_BASE_URL = "http://127.0.0.1:5000/api/auth"


def get_tokens(base_url: str, email: str, password: str) -> tuple[str | None, str | None]:
    print(f"Attempting to login as: {email}")

    login_url = f"{base_url.rstrip('/')}/login"
    payload = {
        "email": email,
        "password": password,
    }

    try:
        data = _post_json(login_url, payload)
        print("\nLogin Successful!")
        print("-" * 50)

        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token")

        print(f"ACCESS TOKEN:\n{access_token}\n")
        print(f"REFRESH TOKEN:\n{refresh_token}\n")

        return access_token, refresh_token

    except Exception as err:
        print(f"\nAn error occurred: {err}")
        sys.exit(1)


def refresh_tokens(base_url: str, refresh_token: str) -> tuple[str | None, str | None]:
    print("\nAttempting to refresh tokens...")

    refresh_url = f"{base_url.rstrip('/')}/refresh"
    headers = {
        "Authorization": f"Bearer {refresh_token}",
    }

    try:
        data = _post_json(refresh_url, None, headers=headers)
        print("\nToken Refresh Successful!")
        print("-" * 50)

        new_access_token = data.get("access_token")
        new_refresh_token = data.get("refresh_token")

        print(f"NEW ACCESS TOKEN:\n{new_access_token}\n")
        print(f"NEW REFRESH TOKEN:\n{new_refresh_token}\n")
        return new_access_token, new_refresh_token

    except Exception as err:
        print(f"\nAn error occurred during refresh: {err}")
        sys.exit(1)


def _post_json(url: str, payload: dict[str, str] | None, headers: dict[str, str] | None = None) -> dict[str, object]:
    request_headers = {
        "Content-Type": "application/json",
        **(headers or {}),
    }
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request_obj = url_request.Request(url=url, data=data, headers=request_headers, method="POST")
    try:
        with url_request.urlopen(request_obj, timeout=30) as response:
            body = response.read().decode("utf-8", errors="replace")
            return json.loads(body)
    except url_error.HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")
        print(f"\nRequest Failed: HTTP {err.code}")
        try:
            print(f"Error Details: {json.loads(body)}")
        except json.JSONDecodeError:
            print(f"Response: {body}")
        sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Get access and refresh tokens for the ticket API")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="Auth API base URL, e.g. https://host/api/auth",
    )
    parser.add_argument("--email", required=True, help="User email")
    parser.add_argument("--password", required=True, help="User password")
    parser.add_argument(
        "--test-refresh",
        action="store_true",
        help="Test the refresh token endpoint after successful login",
    )
    args = parser.parse_args()

    access_token, refresh_token = get_tokens(args.base_url, args.email, args.password)

    if args.test_refresh and refresh_token:
        refresh_tokens(args.base_url, refresh_token)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
