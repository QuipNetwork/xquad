# Copyright (C) 2026 Postquant Labs Incorporated
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Unit tests for xqsa.quip_faucet, mocking the faucet HTTP endpoint."""

from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import patch

import pytest

from xqsa.quip_codec import QuipError
from xqsa.quip_faucet import QuipFaucetError, fund_from_faucet

DEST = "0xdeadbeef"
URL = "http://localhost:20049/api/faucet"


@pytest.fixture(autouse=True)
def _skip_ssl_context(monkeypatch):
    """Stub out CA bundle resolution and TLS context creation; not under test here."""
    monkeypatch.setattr("xqsa.quip_faucet.quip_metadata._default_ca_bundle", lambda: None)
    monkeypatch.setattr("xqsa.quip_faucet.ssl.create_default_context", lambda cafile=None: object())


def _response(body: dict, status: int = 200):
    resp = io.BytesIO(json.dumps(body).encode())
    resp.status = status
    return resp


def _http_error(status: int, body: dict | None, url: str = URL) -> urllib.error.HTTPError:
    raw = json.dumps(body).encode() if body is not None else b"not json"
    return urllib.error.HTTPError(url, status, "error", {}, io.BytesIO(raw))


class _Ctx:
    """Context-manager wrapper mimicking urlopen's return value."""

    def __init__(self, resp):
        self._resp = resp

    def __enter__(self):
        return self._resp

    def __exit__(self, *exc):
        return False


def test_quip_faucet_error_is_quip_error():
    assert issubclass(QuipFaucetError, QuipError)


@patch("xqsa.quip_faucet.urllib.request.urlopen")
def test_success_returns_body(mock_urlopen):
    body = {"amount": 10_000_000_000_000, "dest": DEST}
    mock_urlopen.return_value = _Ctx(_response(body))

    result = fund_from_faucet(DEST, url=URL)

    assert result == body


@patch("xqsa.quip_faucet.urllib.request.urlopen")
def test_request_omits_amount_when_none(mock_urlopen):
    mock_urlopen.return_value = _Ctx(_response({"amount": 1}))

    fund_from_faucet(DEST, url=URL)

    req = mock_urlopen.call_args[0][0]
    sent = json.loads(req.data)
    assert sent == {"dest": DEST}
    assert req.get_method() == "POST"
    assert req.full_url == URL + "/request"


@patch("xqsa.quip_faucet.urllib.request.urlopen")
def test_request_includes_amount_when_set(mock_urlopen):
    mock_urlopen.return_value = _Ctx(_response({"amount": 42}))

    fund_from_faucet(DEST, url=URL, amount=42)

    req = mock_urlopen.call_args[0][0]
    sent = json.loads(req.data)
    assert sent == {"dest": DEST, "amount": 42}


@patch("xqsa.quip_faucet.urllib.request.urlopen")
def test_url_joined_without_double_slash(mock_urlopen):
    mock_urlopen.return_value = _Ctx(_response({"amount": 1}))

    fund_from_faucet(DEST, url=URL + "/")

    req = mock_urlopen.call_args[0][0]
    assert req.full_url == URL + "/request"


@patch("xqsa.quip_faucet.time.sleep")
@patch("xqsa.quip_faucet.urllib.request.urlopen")
def test_rate_limit_retries_once_then_succeeds(mock_urlopen, mock_sleep):
    rate_limited = _http_error(429, {"error": "rate limited", "retry_after_seconds": 4.66})
    success = _Ctx(_response({"amount": 10}))
    mock_urlopen.side_effect = [rate_limited, success]

    result = fund_from_faucet(DEST, url=URL)

    assert result == {"amount": 10}
    assert mock_urlopen.call_count == 2
    mock_sleep.assert_called_once_with(4.66)


@patch("xqsa.quip_faucet.time.sleep")
@patch("xqsa.quip_faucet.urllib.request.urlopen")
def test_rate_limit_twice_raises(mock_urlopen, mock_sleep):
    mock_urlopen.side_effect = [
        _http_error(429, {"error": "rate limited", "retry_after_seconds": 1}),
        _http_error(429, {"error": "rate limited", "retry_after_seconds": 1}),
    ]

    with pytest.raises(QuipFaucetError) as excinfo:
        fund_from_faucet(DEST, url=URL)

    assert excinfo.value.status == 429
    assert mock_urlopen.call_count == 2


@patch("xqsa.quip_faucet.urllib.request.urlopen")
def test_already_funded_raises_without_retry(mock_urlopen):
    mock_urlopen.side_effect = _http_error(
        403, {"error": "destination already funded", "free_balance_plancks": 20_000_000_000_000}
    )

    with pytest.raises(QuipFaucetError) as excinfo:
        fund_from_faucet(DEST, url=URL)

    assert mock_urlopen.call_count == 1
    assert excinfo.value.status == 403
    assert excinfo.value.body["free_balance_plancks"] == 20_000_000_000_000


@patch("xqsa.quip_faucet.urllib.request.urlopen")
def test_bad_amount_raises_with_server_message(mock_urlopen):
    mock_urlopen.side_effect = _http_error(400, {"error": "amount must be a positive integer (plancks)"})

    with pytest.raises(QuipFaucetError) as excinfo:
        fund_from_faucet(DEST, url=URL, amount=-1)

    assert "amount must be a positive integer" in str(excinfo.value)


@patch("xqsa.quip_faucet.urllib.request.urlopen")
def test_url_error_raises_with_no_status(mock_urlopen):
    mock_urlopen.side_effect = urllib.error.URLError("connection refused")

    with pytest.raises(QuipFaucetError) as excinfo:
        fund_from_faucet(DEST, url=URL)

    assert excinfo.value.status is None
    assert excinfo.value.body == {}


@patch("xqsa.quip_faucet.urllib.request.urlopen")
def test_non_json_error_body_tolerated(mock_urlopen):
    mock_urlopen.side_effect = _http_error(500, None)

    with pytest.raises(QuipFaucetError) as excinfo:
        fund_from_faucet(DEST, url=URL)

    assert excinfo.value.status == 500
    assert excinfo.value.body == {}


@patch("xqsa.quip_faucet.urllib.request.urlopen")
def test_non_json_success_body_reads_as_empty(mock_urlopen):
    resp = io.BytesIO(b"<html>not the faucet</html>")
    resp.status = 200
    mock_urlopen.return_value = _Ctx(resp)
    assert fund_from_faucet(DEST, url=URL) == {}


@patch("xqsa.quip_faucet.urllib.request.urlopen")
def test_non_object_error_body_tolerated(mock_urlopen):
    mock_urlopen.side_effect = urllib.error.HTTPError(URL, 400, "error", {}, io.BytesIO(b"[1, 2]"))
    with pytest.raises(QuipFaucetError) as excinfo:
        fund_from_faucet(DEST, url=URL)
    assert excinfo.value.body == {}


@patch("xqsa.quip_faucet.time.sleep")
@patch("xqsa.quip_faucet.urllib.request.urlopen")
def test_rate_limit_beyond_cap_raises_without_sleeping(mock_urlopen, mock_sleep):
    mock_urlopen.side_effect = [_http_error(429, {"retry_after_seconds": 86400})]
    with pytest.raises(QuipFaucetError) as excinfo:
        fund_from_faucet(DEST, url=URL)
    assert excinfo.value.status == 429
    mock_sleep.assert_not_called()


def test_bad_ca_bundle_raises_faucet_error(monkeypatch):
    def boom(cafile=None):
        raise FileNotFoundError(cafile)

    monkeypatch.setattr("xqsa.quip_faucet.ssl.create_default_context", boom)
    with pytest.raises(QuipFaucetError) as excinfo:
        fund_from_faucet(DEST, url=URL)
    assert excinfo.value.status is None
