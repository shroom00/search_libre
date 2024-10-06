import re
from urllib.parse import urlsplit
from scrapy import Request, Spider
from scrapy.crawler import Crawler
from scrapy.exceptions import IgnoreRequest


import mimetypes
from typing import List, Tuple

from scrapy.http import Response

from crawler.custom_signals import TLD_FILTER_CHECK, URL_FILTER_CHECK


class MimetypeFilter:
    """
    Filters requests based on a list of mimetypes.

    Possible mimetypes qre the values (not keys) found in `mimetypes.types_map`.
    Internally, it uses `mimetypes.guess_type(url, strict=True)`.

    Settings:
        ALLOWED_MIMETYPES should be a list of allowed mimetypes.
        DEFAULT_MIMETYPE is the mimetype to default to if one isn't found.
    """

    @classmethod
    def from_crawler(cls, crawler: Crawler):
        allowed_mimetypes = crawler.settings.getlist("ALLOWED_MIMETYPES")
        default_mimetype = crawler.settings.get("DEFAULT_MIMETYPE")
        if not allowed_mimetypes:
            raise ValueError("ALLOWED_MIMETYPES must be set.")
        elif default_mimetype is None:
            raise ValueError("DEFAULT_MIMETYPE must be set.")

        return cls(allowed_mimetypes, default_mimetype)

    def __init__(self, allowed_mimetypes: List[str], default_mimetype: str) -> None:
        if invalid_mimetypes := [
            m for m in allowed_mimetypes if m not in mimetypes.types_map.values()
        ]:
            raise ValueError(
                f"The following mimetypes are invalid: {invalid_mimetypes}"
            )
        elif default_mimetype not in mimetypes.types_map.values():
            raise ValueError(f"Default mimetype ({default_mimetype}) is not valid.")
        self.allowed_mimetypes = allowed_mimetypes
        self.default_mimetype = default_mimetype

    def process_request(self, request: Request, spider: Spider):
        guessed = mimetypes.guess_type(request.url)[0] or self.default_mimetype
        if guessed in self.allowed_mimetypes:
            return None
        raise IgnoreRequest(f"{request.url} has the incorrect mimetype.")


class CssFilter:
    # allow_root in CSS_FILTERS exists so I can index libreddit/libred (alt reddit frontend) sites' home page without indexing all of reddit.
    """
    This middleware filters responses based on CSS selectors.
    It depends on the setting `CSS_FILTERS`.

    `CSS_FILTERS` should look something like this:
        [(selector, allow_root)]

        e.g. [('href^="/private', false)]

    If `allow_root` is true, the response will be allowed if the response url has no path.
    Otherwise, it is ignored.

    If a match is found, the response is ignored.
    Selectors can be tested using scrapy.http.HtmlResponse().css(selector)
    """

    @classmethod
    def from_crawler(cls, crawler: Crawler):
        selectors = crawler.settings.getlist("CSS_FILTERS")
        if selectors is None:
            raise ValueError("CSS_FILTERS must be set.")
        return cls(selectors)

    def __init__(self, selectors: List[Tuple[str, bool]]) -> None:
        if not selectors:
            raise ValueError(f"Have you added any selectors? {selectors = }")
        for selector in selectors:
            if (
                not isinstance(selector, tuple)
                or len(selector) != 2
                or not isinstance(selector[0], str)
                or not isinstance(selector[1], bool)
            ):
                raise TypeError(
                    "Selectors must be tuples containing a string and boolean value."
                )
        self.selectors = selectors

    def process_response(self, request: Request, response: Response, spider: Spider):
        split_url = urlsplit(response.url)
        for selector, allow_root in self.selectors:
            if response.css(selector):
                if allow_root:
                    return request.replace(url=split_url._replace(path="").geturl())
                if (not allow_root) or (split_url.path not in ("", "/")):
                    raise IgnoreRequest(response.url)
        return response


class URLFilter:
    """
    This middleware filters urls.
    Requires setting: `URL_WHITELIST` or `URL_BLACKLIST`.
    The url lists should be lists of regex patterns (as strings).
    The url scheme should not be included in the patterns, it is assumed to be http(s).
    At least one of the lists should be defined.
    If both `URL_WHITELIST` and `URL_BLACKLIST` are set, `URL_WHITELIST` is used.
    """

    @classmethod
    def from_crawler(cls, crawler: Crawler):
        whitelist = crawler.settings.getlist("URL_WHITELIST")
        blacklist = crawler.settings.getlist("URL_BLACKLIST")
        if (whitelist, blacklist) == (None, None):
            raise ValueError("Either ALLOWED_TLDS or DISALLOWED_TLDS must be set.")

        o = cls(whitelist, blacklist)
        crawler.signals.connect(o.should_crawl, URL_FILTER_CHECK)

        return o

    def __init__(self, whitelist: List[str], blacklist: List[str]) -> None:
        self.whitelist = (
            re.compile(r"https?://(" + r"|".join(whitelist) + r")")
            if whitelist
            else None
        )
        self.blacklist = (
            re.compile(r"https?://(" + r"|".join(blacklist) + r")")
            if blacklist
            else None
        )

    def process_request(self, request: Request, spider: Spider):
        url = request.url
        spider.log(f"trying to filter request {url} by blacklist ...")
        if self.whitelist and re.match(self.whitelist, url):
            return None
        elif self.blacklist and not re.match(self.blacklist, url):
            return None
        raise IgnoreRequest(f"{url} isn't allowed.")

    def process_response(self, request: Request, response: Response, spider: Spider):
        url = response.url
        spider.log(f"trying to filter response {url} by blacklist ...")
        if self.should_crawl(url):
            return response
        raise IgnoreRequest(f"{url} isn't allowed.")

    def should_crawl(self, url) -> bool:
        if self.whitelist and re.match(self.whitelist, url):
            return True
        elif self.blacklist and not re.match(self.blacklist, url):
            return True
        return False


class TLDFilter:
    """
    This middleware filters request urls based on their TLDs.
    Requires setting: `ALLOWED_TLDS` or `DISALLOWED_TLDS`.
    If `ALLOWED_TLDS` is set, all TLDs are assumed to be invalid by default.
    If `DISALLOWED_TLDS` is set, all TLDs are assumed to be valid by default.
    If both `ALLOWED_TLDS` and `DISALLOWED_TLDS` are set, `ALLOWED_TLDS` is used.
    """

    @classmethod
    def from_crawler(cls, crawler: Crawler):
        allowed_tlds = crawler.settings.getlist("ALLOWED_TLDS", None)
        disallowed_tlds = crawler.settings.getlist("DISALLOWED_TLDS", None)
        if (allowed_tlds, disallowed_tlds) == (None, None):
            raise ValueError("Either ALLOWED_TLDS or DISALLOWED_TLDS must be set.")
        elif allowed_tlds is not None:
            allowed_tlds = tuple(allowed_tlds)
            disallowed_tlds = tuple(disallowed_tlds or [])
        elif disallowed_tlds is not None:
            allowed_tlds = tuple(allowed_tlds or [])
            disallowed_tlds = tuple(disallowed_tlds)

        o = cls(allowed_tlds, disallowed_tlds)
        crawler.signals.connect(o.should_crawl, TLD_FILTER_CHECK)

        return o

    def __init__(self, allowed_tlds: Tuple[str], disallowed_tlds: Tuple[str]):
        self.allowed_tlds = allowed_tlds
        self.disallowed_tlds = disallowed_tlds
        self.tld_pattern: re.Pattern
        "Pattern to match netloc against to see if a url has a valid tld"
        if self.allowed_tlds and self.disallowed_tlds:
            raise ValueError(
                "Do not define allowed_urls AND disallowed_urls. Define one or the other."
            )
        elif self.allowed_tlds:
            self.tld_pattern = re.compile(
                r".*\.(" + r"|".join(self.allowed_tlds) + r")$"
            )
        else:
            self.tld_pattern = re.compile(
                r".*\.(?!" + r"|".join(self.disallowed_tlds) + r")$"
            )

    def process_request(self, request: Request, spider: Spider):
        if self.should_crawl(request.url):
            return None
        raise IgnoreRequest(f"{request.url} doesn't have a valid TLD.")

    def should_crawl(self, url: str) -> bool:
        # we check for http/https on the off chance that we find ftp urls (etc.)
        # it's unlikely but better safe than sorry?
        if not url.lower().startswith("http"):
            return False
        parsed_url = urlsplit(url)
        return re.match(self.tld_pattern, parsed_url.netloc)
