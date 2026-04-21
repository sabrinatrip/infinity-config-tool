"""
Minimal Avaya Infinity / core portal client: OAuth browser login + session cookies,
then REST calls under https://{host}/api/.

Extracted from infmig-axp-config-data-master/infinity.py (perform_oauth_login,
parse_form_fields, list_users) for standalone use.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


def parse_form_fields(html: str, field_id: Optional[str]) -> Tuple[str, Dict[str, str]]:
    """
    Parse the first <form> in `html`, returning its action URL and a dict of
    all <input name=… value=…> pairs (missing values default to "").
    """
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form")
    if form is None:
        raise ValueError("No <form> element found in HTML")

    if field_id is None:
        input_selector = soup.find("input")
    else:
        input_selector = soup.find("input", {"id": field_id})
    if input_selector is None:
        raise ValueError("No <input> element found in HTML")

    action = form.get("action")
    if not action:
        raise ValueError("Form is missing an action attribute")

    data: Dict[str, str] = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        data[name] = inp.get("value", "")

    return action, data


class InfinityClient:
    def __init__(self, host: str, username: str, password: str):
        self.host = host.rstrip("/")
        self.username = username
        self.password = password
        self.session: Optional[requests.Session] = None

    def get_url(self) -> str:
        return f"https://{self.host}/api/"

    def _ensure_token_valid(self) -> None:
        pass

    def perform_oauth_login(self) -> None:
        self.session = requests.Session()
        initial_url = f"https://{self.host}/app/agent/"
        self.session.trust_env = False

        try:
            response = self.session.get(initial_url)
            response.raise_for_status()
        except requests.RequestException as e:
            raise ValueError(f"Failed to reach login page: {e}") from e

        html = response.text
        initial_base_url = response.url

        try:
            form_action, form_data = parse_form_fields(html, None)
        except Exception as e:
            raise ValueError(f"Error parsing login form: {e}") from e

        form_data["username"] = self.username
        form_url = urljoin(initial_base_url, form_action)
        res = self.session.post(form_url, data=form_data)
        base_url = res.url
        html = res.text

        try:
            form_action, form_data = parse_form_fields(html, "password")
            form_url = urljoin(base_url, form_action)
        except Exception as e:
            raise ValueError(f"Error parsing password form: {e}") from e

        form_data["password"] = self.password
        login_res = self.session.post(form_url, data=form_data)
        login_res.raise_for_status()

        form_url = urljoin(login_res.url, "/app/agent/auth")
        auth_res = self.session.post(form_url, auth=None)
        auth_res.raise_for_status()

    def list_users(self) -> Optional[List[Any]]:
        self._ensure_token_valid()
        if not self.session:
            raise RuntimeError("Not logged in; call perform_oauth_login() first.")
        url = self.get_url() + "core/v4/users"
        response = self.session.get(url)
        if response.status_code != 200:
            raise ValueError(
                f"Failed to list users: {response.status_code} {response.text}"
            )
        return response.json().get("users")

    def list_queue_folders(self, params: Optional[Dict[str, Any]] = None) -> Any:
        """
        GET core/v4/folders/queues — folder tree for Queues (same as Admin UI), e.g.:
        .../api/core/v4/folders/queues?parentFolderId=null&includeItems=true&...
        """
        self._ensure_token_valid()
        if not self.session:
            raise RuntimeError("Not logged in; call perform_oauth_login() first.")
        url = self.get_url() + "core/v4/folders/queues"
        default_params: Dict[str, Any] = {
            "parentFolderId": "null",
            "includeItems": "true",
            "includeExtendedRecordDetails": "true",
            "type": "all",
            "includeStandard": "true",
            "basePath": "/queues",
            "baseLabel": "Queues",
            "currentPath": "/queues",
        }
        merged = {**default_params, **(params or {})}
        response = self.session.get(url, params=merged)
        if response.status_code != 200:
            raise ValueError(
                f"Failed to list queue folders: {response.status_code} {response.text}"
            )
        return response.json()
