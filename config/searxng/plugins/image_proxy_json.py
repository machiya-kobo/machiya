# SPDX-License-Identifier: AGPL-3.0-or-later
"""Image proxy for JSON results.

SearXNG's HTML templates run every image through ``image_proxify`` (``/image_proxy?url=..&h=..``), but
``format=json`` returns the raw thumbnail URLs, so a JSON client such as Shiori's combined results page
would fetch thumbnails straight from Google, Brave and Wikimedia. This plugin rewrites the image fields
of JSON responses to the same signed ``/image_proxy`` links, so every thumbnail is fetched by SearXNG,
through its outgoing proxy (if one is set, e.g. a VPN proxy), and the signing key never leaves the server.

The link is built exactly as ``searx.webapp.image_proxify`` builds it (HMAC-SHA256 of the URL with
``server.secret_key`` via ``searx.webutils.new_hmac``, urlencoded ``url`` + ``h``), so the existing
``/image_proxy`` endpoint accepts it unchanged. Links are absolute (``server.base_url``).
Mounted into the image as ``searx/plugins/image_proxy_json.py``; enabled in settings.yml ``plugins:``.
Re-check after a SearXNG upgrade: it relies on the plugin API and on ``new_hmac``.
"""
# pylint: disable=missing-class-docstring, unused-argument

import logging
import typing as t
from urllib.parse import urlencode

from searx import settings
from searx.webutils import new_hmac

from . import Plugin, PluginInfo

if t.TYPE_CHECKING:
    from searx.extended_types import SXNG_Request
    from searx.plugins import PluginCfg
    from searx.result_types import Result
    from searx.search import SearchWithPlugins

log = logging.getLogger("searx.plugins.image_proxy_json")

FIELDS = ("thumbnail", "thumbnail_src", "img_src")
IMAGE_DATA = ("gif", "png", "jpeg", "pjpeg", "webp", "tiff", "bmp")


def proxify(url: t.Any) -> t.Any:
    """Signed absolute /image_proxy link for url; anything else is returned unchanged."""
    if not isinstance(url, str) or not url:
        return url
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith("data:image/"):
        head = url[len("data:image/"):50].split(";")
        if len(head) == 2 and head[0] in IMAGE_DATA and head[1].startswith("base64,"):
            return url                      # inline image: nothing to fetch
        return None                         # same rule as image_proxify: other data: URLs are dropped
    if not url.startswith(("http://", "https://")):
        return url
    base = (settings["server"].get("base_url") or "/").rstrip("/")
    if url.startswith(base + "/image_proxy?"):
        return url                          # already proxied (merged infoboxes pass through twice)
    query = urlencode({"url": url.encode(), "h": new_hmac(settings["server"]["secret_key"], url.encode())})
    return f"{base}/image_proxy?{query}"


def is_json(request: "SXNG_Request") -> bool:
    form = getattr(request, "form", None) or {}
    return form.get("format") == "json"


@t.final
class SXNGPlugin(Plugin):
    """Route thumbnails in JSON results through SearXNG's image proxy."""

    id = "image_proxy_json"

    def __init__(self, plg_cfg: "PluginCfg") -> None:
        super().__init__(plg_cfg)
        self.info = PluginInfo(
            id=self.id,
            name="Image proxy for JSON results",
            description="Rewrites thumbnail URLs in format=json responses to signed /image_proxy links",
            preference_section="privacy",
        )

    def on_result(self, request: "SXNG_Request", search: "SearchWithPlugins", result: "Result") -> bool:
        if not settings["server"].get("image_proxy") or not is_json(request):
            return True
        for field in FIELDS:
            try:                            # LegacyResult is dict-like, typed results use attributes
                value = result[field] if hasattr(result, "__getitem__") and field in result else getattr(result, field, None)
            except Exception:  # pylint: disable=broad-except
                value = getattr(result, field, None)
            if not value:
                continue
            new = proxify(value)
            if new == value:
                continue
            try:
                if hasattr(result, "__setitem__") and not hasattr(type(result), field):
                    result[field] = new
                else:
                    setattr(result, field, new)
            except Exception as exc:  # pylint: disable=broad-except
                log.debug("could not rewrite %s: %s", field, exc)
        return True

    def post_search(self, request: "SXNG_Request", search: "SearchWithPlugins") -> None:
        if not settings["server"].get("image_proxy") or not is_json(request):
            return None
        for box in getattr(search.result_container, "infoboxes", []) or []:
            try:
                if box.get("img_src"):
                    box["img_src"] = proxify(box["img_src"])
            except Exception as exc:  # pylint: disable=broad-except
                log.debug("could not rewrite infobox img_src: %s", exc)
        return None
