"""Small HTTP helpers with polite rate limits. No third-party packages."""

import gzip
import json
import os
import time
import urllib.error
import urllib.request


class FetchError(Exception):
    pass


class SecFetcher:
    """SEC EDGAR client. SEC requires a User-Agent naming the requester with an
    e-mail address and allows at most 10 requests a second."""

    def __init__(self, user_agent=None, offline_dir=None, pause=0.12):
        self.ua = (user_agent or os.environ.get("SEC_USER_AGENT") or "").strip()
        self.offline_dir = offline_dir
        self.pause = pause
        self._last = 0.0

    def json(self, url, offline_name):
        if self.offline_dir:
            path = os.path.join(self.offline_dir, offline_name)
            if not os.path.isfile(path):
                raise FetchError("offline: no file %s" % path)
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        if "@" not in self.ua:
            raise FetchError("Set SEC_USER_AGENT to 'Company Name contact@example.com' before calling SEC.")
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": self.ua, "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
        except urllib.error.HTTPError as exc:
            raise FetchError("HTTP %s for %s" % (exc.code, url))
        except urllib.error.URLError as exc:
            raise FetchError("could not reach %s: %s" % (url, exc.reason))
        return json.loads(data.decode("utf-8"))


def get_json(url, timeout=60, missing=False):
    """Plain JSON GET for public endpoints that need no identification (IEX HIST index).

    With missing=True a 404 returns None instead of raising.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "stocksense-pipeline"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if missing and exc.code == 404:
            return None
        raise FetchError("could not fetch %s: %s" % (url, exc))
    except urllib.error.URLError as exc:
        raise FetchError("could not fetch %s: %s" % (url, exc))
